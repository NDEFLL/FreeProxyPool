"""代理池：JSON 持久化、评分淘汰、加权随机取用。"""

# 标准库：JSON 读写、日志、原子替换、加权随机、线程锁、时间
import json
import logging
import os
import random
import threading
import time
from pathlib import Path

# 包内模块：配置、采集、地址规范化、并发校验
from . import config
from .sources import fetch_all
from .utils import normalize_proxy
from .validator import check_many

# 当前模块的日志器
logger = logging.getLogger(__name__)


class ProxyPool:
    """代理池：负责代理的加载、保存、评分、淘汰和取用。"""

    def __init__(self, store_file: str | Path | None = None):
        """初始化代理池。

        store_file   代理数据文件，默认 config.STORE_FILE
        _lock        线程锁，保证同一时间只有一个线程修改数据
        _records     内存中的代理池{"ip:port": {...}}
        _rejected    失败代理缓存{"ip:port": 失败时间戳}
        _updated_at  最后一次保存/更新的时间戳
        load() 构造时立即从磁盘恢复上次的代理和评分
        """
        self.store_file = Path(store_file) if store_file else config.STORE_FILE
        self._lock = threading.Lock()
        self._records: dict[str, dict] = {}
        self._rejected: dict[str, float] = {}
        self._updated_at: float | None = None
        self.load()

    # ---------- 持久化 ----------
    def load(self) -> None:
        """从数据文件恢复代理池。
        只还原本地状态：读取 JSON、规范化代理地址、补默认字段、恢复失败记录，
        """
        # 数据文件不存在，说明还没采集过，保持空池
        if not self.store_file.exists():
            return
        # 读取并解析 JSON
        try:
            data = json.loads(self.store_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("读取代理池文件失败: %s", exc)
            return
        # 当前时间，用来过滤过期的失败记录
        now = time.time()
        # 修改内存数据需要加锁
        with self._lock:
            # 逐条恢复代理记录
            for item in data.get("proxies", []):
                # 规范化地址，兼容 "http://ip:port" 等写法
                proxy = normalize_proxy(item.get("proxy"))
                # 地址非法就跳过
                if not proxy:
                    continue
                # 写入内存池，缺失字段用默认值补齐
                self._records[proxy] = {
                    "proxy": proxy,
                    # 是否支持 HTTPS
                    "https": bool(item.get("https", False)),
                    # 是否支持 HTTP，旧数据默认 True
                    "http": bool(item.get("http", True)),
                    # 最近一次校验的延迟（毫秒）
                    "latency": item.get("latency"),
                    # 质量分，旧数据缺失时用初始分
                    "score": int(item.get("score", config.INIT_SCORE)),
                    # 累计成功次数
                    "success": int(item.get("success", 0)),
                    # 累计失败次数
                    "fail": int(item.get("fail", 0)),
                    # 最近一次校验时间
                    "last_checked": item.get("last_checked"),
                }
            # 恢复失败记录，只保留 24 小时内的
            for proxy, ts in (data.get("rejected") or {}).items():
                if now - ts < 86400:
                    self._rejected[proxy] = ts
            # 恢复最后更新时间
            self._updated_at = data.get("updated_at")
        # 打印加载结果
        logger.info("已加载代理池: %d 个代理", len(self._records))

    def save(self) -> None:
        """把当前代理池写入 JSON 文件。"""
        # 读写文件期间不允许其他线程修改数据
        with self._lock:
            payload = {
                "updated_at": time.time(),
                # 按分数降序、延迟升序排列，方便人工查看
                "proxies": sorted(
                    self._records.values(),
                    key=lambda r: (-r["score"], r.get("latency") or 99999),
                ),
                # 失败代理缓存
                "rejected": self._rejected,
            }
            # 确保父目录存在
            self.store_file.parent.mkdir(parents=True, exist_ok=True)
            # 先写临时文件，再原子替换，避免写到一半损坏原文件
            tmp_file = self.store_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 用临时文件替换正式文件
            os.replace(tmp_file, self.store_file)
            # 记录本次保存时间
            self._updated_at = payload["updated_at"]

    # ---------- 记录维护 ----------
    @staticmethod
    def _new_record(proxy: str, result: dict) -> dict:
        """校验通过的新代理，生成一条初始记录。"""
        return {
            # 代理地址 "ip:port"
            "proxy": proxy,
            # 是否支持 HTTPS
            "https": bool(result.get("https")),
            # 是否支持 HTTP
            "http": bool(result.get("http")),
            # 本次校验延迟（毫秒）
            "latency": result.get("latency"),
            # 初始分：INIT_SCORE + SUCCESS_GAIN，且不超过 MAX_SCORE
            "score": min(config.MAX_SCORE, config.INIT_SCORE + config.SUCCESS_GAIN),
            # 成功次数从 1 开始
            "success": 1,
            # 失败次数从 0 开始
            "fail": 0,
            # 本次校验时间
            "last_checked": int(time.time()),
        }

    @staticmethod
    def _apply_success(record: dict, result: dict) -> None:
        """代理再次校验通过：更新协议、延迟，加分并记录成功次数。"""
        # 更新协议支持情况
        record["https"] = bool(result.get("https"))
        record["http"] = bool(result.get("http"))
        # 更新最近延迟
        record["latency"] = result.get("latency")
        # 加分，但有上限
        record["score"] = min(config.MAX_SCORE, record["score"] + config.SUCCESS_GAIN)
        # 成功次数 +1
        record["success"] += 1
        # 更新最近校验时间
        record["last_checked"] = int(time.time())

    def _apply_failure(self, record: dict) -> None:
        """一次失败：扣分、累计失败次数；分数降到 0 以下就淘汰。"""
        # 扣分
        record["score"] -= config.FAIL_PENALTY
        # 累计失败次数
        record["fail"] += 1
        # 更新最近失败时间
        record["last_checked"] = int(time.time())
        # 分数小于等于 0，移出池并加入失败缓存
        if record["score"] <= 0:
            self._records.pop(record["proxy"], None)
            self._rejected[record["proxy"]] = time.time()

    def _trim(self) -> None:
        """池子超过容量时，保留分数高、延迟低的代理。"""
        # 未超限，不用处理
        if len(self._records) <= config.MAX_POOL_SIZE:
            return
        # 按分数降序、延迟升序排序，取前 MAX_POOL_SIZE 个
        keep = sorted(
            self._records.values(),
            key=lambda r: (-r["score"], r.get("latency") or 99999),
        )[: config.MAX_POOL_SIZE]
        # 用保留的记录重建字典
        self._records = {record["proxy"]: record for record in keep}

    def _purge_rejected(self, now: float) -> None:
        """清理 24 小时前的失败记录，避免文件无限增长。"""
        self._rejected = {
            proxy: ts for proxy, ts in self._rejected.items() if now - ts < 86400
        }

    # ---------- 刷新 ----------
    def refresh(self) -> dict:
        """采集新代理并重新校验，更新评分后落盘。"""
        # 记录开始时间，用于统计耗时
        started = time.time()
        logger.info("开始抓取免费代理源 ...")
        # 并发抓取所有源，返回去重后的代理集合
        fetched = fetch_all()
        now = time.time()

        # 读取当前池和失败缓存，准备筛选候选
        with self._lock:
            # 池内已有代理
            known = set(self._records)
            # 30 分钟内失败过的代理
            rejected = {
                proxy
                for proxy, ts in self._rejected.items()
                if now - ts < config.REJECT_TTL
            }
        # 新代理 = 抓到的 - 池内已有 - 近期失败过
        fresh = [p for p in fetched if p not in known and p not in rejected]
        # 单轮限制新增数量，避免一次校验太多
        fresh = fresh[: config.MAX_NEW_PER_CYCLE]
        # 本轮要校验的对象：池内全部 + 新代理
        targets = list(known) + fresh

        logger.info(
            "开始校验 %d 个代理（池内 %d，新增 %d）", len(targets), len(known), len(fresh)
        )
        # 本轮可用数量、其中新增可用数量
        alive = new_alive = 0
        # 并发校验并逐个处理结果
        for done, result in enumerate(check_many(targets), 1):
            proxy = result["proxy"]
            with self._lock:
                record = self._records.get(proxy)
                # 校验通过
                if result["ok"]:
                    alive += 1
                    # 新代理：创建记录
                    if record is None:
                        self._records[proxy] = self._new_record(proxy, result)
                        new_alive += 1
                    # 老代理：更新记录、加分
                    else:
                        self._apply_success(record, result)
                # 新代理校验失败：加入失败缓存
                elif record is None:
                    self._rejected[proxy] = time.time()
                # 老代理校验失败：扣分，可能淘汰
                else:
                    self._apply_failure(record)
            # 每 200 个打印一次进度
            if done % 200 == 0:
                logger.info("已校验 %d/%d", done, len(targets))

        # 校验结束后统一做容量截断和失败缓存清理
        with self._lock:
            self._trim()
            self._purge_rejected(time.time())
            pool_size = len(self._records)
        # 落盘
        self.save()

        # 汇总本轮结果
        summary = {
            # 抓取到的去重代理数
            "fetched": len(fetched),
            # 实际校验的代理数
            "checked": len(targets),
            # 校验通过的数量
            "alive": alive,
            # 其中新增的数量
            "new_alive": new_alive,
            # 刷新后的池大小
            "pool_size": pool_size,
            # 本轮耗时（秒）
            "elapsed": round(time.time() - started, 1),
        }
        logger.info(
            "刷新完成：可用 %d，其中新增 %d，池内共 %d，耗时 %.1fs",
            alive,
            new_alive,
            pool_size,
            summary["elapsed"],
        )
        return summary

    # ---------- 取用 ----------
    def pick(self, https_only: bool = False, exclude: set[str] | None = None) -> dict | None:
        """按分数加权随机取一个代理；没有可用代理时返回 None。"""
        # exclude 为空时用空集合，方便后面判断
        exclude = exclude or set()
        # 复制候选，避免把内部记录直接暴露出去
        with self._lock:
            candidates = [
                dict(record)
                for record in self._records.values()
                if record["proxy"] not in exclude
                and (not https_only or record.get("https"))
            ]
        # 没有候选代理
        if not candidates:
            return None
        # 权重就是分数，分数越高越容易被选中；最低按 1 算
        weights = [max(record["score"], 1) for record in candidates]
        # 加权随机取 1 个
        return random.choices(candidates, weights=weights, k=1)[0]

    def pick_many(self, count: int = 1, https_only: bool = False) -> list[dict]:
        """按分数加权随机取多个不重复的代理。"""
        picked: list[dict] = []
        # 已选中的代理，避免重复
        used: set[str] = set()
        # 逐个抽取，每次排除已选的
        for _ in range(count):
            with self._lock:
                candidates = [
                    dict(record)
                    for record in self._records.values()
                    if record["proxy"] not in used
                    and (not https_only or record.get("https"))
                ]
            # 候选不够就提前结束
            if not candidates:
                break
            # 分数作为权重
            weights = [max(record["score"], 1) for record in candidates]
            # 加权随机取 1 个
            chosen = random.choices(candidates, weights=weights, k=1)[0]
            # 记录已选，继续下一个
            used.add(chosen["proxy"])
            picked.append(chosen)
        return picked

    def list_proxies(self, limit: int | None = None, https_only: bool = False) -> list[dict]:
        """按分数、延迟排序返回代理列表，可选只返回 HTTPS。"""
        with self._lock:
            items = [
                dict(record)
                for record in self._records.values()
                if not https_only or record.get("https")
            ]
        # 分数降序，延迟升序
        items.sort(key=lambda r: (-r["score"], r.get("latency") or 99999))
        # 有 limit 就截断，否则返回全部
        return items[:limit] if limit else items

    def report_failure(self, proxy: str) -> None:
        """运行时反馈一次失败：扣分，分数耗尽后淘汰"""
        # 规范化传入的代理地址
        normalized = normalize_proxy(proxy)
        # 地址非法，直接忽略
        if not normalized:
            return
        with self._lock:
            record = self._records.get(normalized)
            # 池里没有这个代理，直接记入失败缓存
            if record is None:
                self._rejected[normalized] = time.time()
            # 池里有，扣分并判断是否淘汰
            else:
                self._apply_failure(record)
        self.save()

    # ---------- 统计 ----------
    def stats(self) -> dict:
        """返回代理池概况。"""
        with self._lock:
            records = list(self._records.values())
        # 取出有延迟数据的记录
        latencies = [r["latency"] for r in records if r.get("latency") is not None]
        return {
            # 代理总数
            "total": len(records),
            # 支持 HTTPS 的数量
            "https": sum(1 for r in records if r.get("https")),
            # 平均延迟（毫秒），没有延迟数据时为 None
            "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
            # 最后更新时间
            "updated_at": self._updated_at,
        }
