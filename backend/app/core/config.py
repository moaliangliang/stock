"""
全局配置管理 - 使用 Pydantic Settings 从环境变量加载
"""
from typing import List, Optional
try:
    from pydantic_settings import BaseSettings
    from pydantic import field_validator
except ImportError:
    from pydantic import BaseSettings, validator as field_validator
from pydantic import AnyHttpUrl


class Settings(BaseSettings):
    # 应用基础配置
    APP_NAME: str = "Quantitative Trading Platform"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24小时

    # 数据库
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "quant_trade"
    POSTGRES_USER: str = "quant_user"
    POSTGRES_PASSWORD: str = ""

    @property
    def DATABASE_URL(self) -> str:
        if self.DEBUG:
            return "sqlite+aiosqlite:///./quant_trade.db"
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def SYNC_DATABASE_URL(self) -> str:
        if self.DEBUG:
            return "sqlite:///./quant_trade.db"
        return f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_DB: int = 0

    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    # Celery
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # 行情
    MARKET_DATA_PROVIDER: str = "sina"  # sina | eastmoney | eastmoney_first | mock
    MARKET_DATA_UPDATE_INTERVAL: int = 5
    DATA_AUTHENTICITY_STRICT: bool = False  # 严格模式：禁止降级到 mock，数据获取失败直接报错
    EASTMONEY_SKILLS_API_KEY: str = ""  # 东方财富妙想Skills API密钥
    EASTMONEY_SKILLS_BASE_URL: str = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"

    # Bark 推送通知
    BARK_KEY: str = ""

    # 交叉校验（多渠道数据对比）
    CROSS_VALIDATION_ENABLED: bool = False  # 主开关：开启后同时从第二数据源拉取并对比
    QUOTE_PRICE_DISCREPANCY_THRESHOLD: float = 0.02   # 行情价格字段差异阈值（2%）
    QUOTE_VOLUME_DISCREPANCY_THRESHOLD: float = 0.05  # 成交量/成交额差异阈值（5%）
    KLINE_PRICE_DISCREPANCY_THRESHOLD: float = 0.02   # K线价格差异阈值（2%）
    KLINE_VOLUME_DISCREPANCY_THRESHOLD: float = 0.10  # K线成交量差异阈值（10%）
    DISCREPANCY_MODERATE_THRESHOLD: float = 0.01       # 差异 >=1% → moderate
    DISCREPANCY_CRITICAL_THRESHOLD: float = 0.05       # 差异 >=5% → critical

    # 交易
    ORDER_EXECUTION_MODE: str = "sandbox"  # sandbox | eastmoney
    EM_TRADE_AGENT_URL: str = "http://127.0.0.1:8520"  # Windows easytrader 代理地址
    DEFAULT_MAX_POSITION_RATIO: float = 0.3
    DEFAULT_MAX_DAILY_LOSS: float = 0.05
    DEFAULT_STOP_LOSS_RATIO: float = 0.02

    # 自动交易
    AUTO_TRADE_ENABLED: bool = False         # 总开关：是否启用自动交易
    AUTO_TRADE_DRY_RUN: bool = True          # 干跑模式：只记录不实际下单（首次启用建议先开）
    AUTO_TRADE_MIN_LEVEL: str = "STRONG_BUY" # 触发自动交易的最低信号级别: STRONG_BUY | BUY
    AUTO_TRADE_MAX_PER_ORDER: float = 50000  # 单笔自动交易最大金额（元）
    AUTO_TRADE_MAX_DAILY_ORDERS: int = 5     # 每日自动交易最大笔数
    AUTO_TRADE_POSITION_PCT: float = 0.1     # 单只股票自动交易仓位占比（相对总资金）

    # CORS
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:3000"

    @property
    def CORS_ORIGINS_LIST(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    # 日志
    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = "logs"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
