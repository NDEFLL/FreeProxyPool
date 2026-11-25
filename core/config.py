"""代理池配置"""

from pathlib import Path

# 当前文件在 core/ 下，上一级就是 proxy_pool/
BASE_DIR = Path(__file__).resolve().parent.parent
# 数据目录，存放代理池文件
DATA_DIR = BASE_DIR / "data"
# 代理池持久化文件
STORE_FILE = DATA_DIR / "proxies.json"

# ---- 采集 ----
# 抓取代理源时使用的请求头
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

FETCH_TIMEOUT = 15  # 单个源请求超时时间（秒）

FETCH_WORKERS = 6   # 同时抓取多少个源

MAX_PER_SOURCE = 150    # 单个源最多保留多少条代理，超过就随机抽取，避免大源占满候选

MAX_NEW_PER_CYCLE = 1500    # 每轮最多校验多少个新代理，控制单次刷新耗时

# ---- 校验 ----
VALIDATE_TIMEOUT = 5    # 校验单个代理的超时时间（秒）
CHECK_WORKERS = 100  # 并发校验的线程数
# HTTPS 代理的校验目标：页面小、国内直连稳定、同时支持 http/https
HTTPS_CHECK_URL = "https://www.baidu.com/robots.txt"
HTTP_CHECK_URL = "http://www.baidu.com/robots.txt"
# 响应中必须包含的特征串，用来确认请求真的到达目标站点而不是被劫持
CHECK_MARKER = b"Baiduspider"

# ---- 评分与容量 ----
INIT_SCORE = 60     # 新代理的初始分数

MAX_SCORE = 100     # 分数上限，校验成功不会超过这个值

SUCCESS_GAIN = 12   # 每次校验成功加多少分

FAIL_PENALTY = 30   # 每次失败扣多少分，扣到 0 以下就淘汰

MAX_POOL_SIZE = 500     # 池内最多保留多少个代理，超出按分数和延迟截断

REJECT_TTL = 1800   # 校验失败的代理在该时间内不再重复校验（秒），默认 30 分钟

# ---- 定时刷新 ----
# 自动刷新间隔（秒），只在 serve 模式下生效，默认 10 分钟
REFRESH_INTERVAL = 600
