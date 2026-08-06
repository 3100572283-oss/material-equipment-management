"""
Redis 缓存工具层
- 封装常用的缓存读写操作
- Redis 不可用时自动降级为直接返回 None（不影响系统运行）
- 支持权限缓存、系统配置缓存、Dashboard统计缓存
"""
import json
import logging
from functools import wraps

logger = logging.getLogger(__name__)

# 全局 Redis 客户端（延迟初始化）
_redis_client = None
_redis_available = None  # None=未检测, True/False


def _get_redis():
    """获取 Redis 客户端，不可用返回 None"""
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis
        from flask import current_app
        host = current_app.config.get('REDIS_HOST', 'localhost')
        port = current_app.config.get('REDIS_PORT', 6379)
        db = current_app.config.get('REDIS_DB', 0)
        password = current_app.config.get('REDIS_PASSWORD', None)
        _redis_client = redis.Redis(
            host=host, port=port, db=db, password=password,
            socket_connect_timeout=2, socket_timeout=2,
            decode_responses=True
        )
        _redis_client.ping()
        _redis_available = True
        logger.info("Redis 缓存层已连接: %s:%s/%s", host, port, db)
    except Exception as e:
        _redis_available = False
        logger.warning("Redis 不可用，降级为直接查询: %s", e)
    return _redis_client if _redis_available else None


def cache_get(key):
    """读取缓存，返回字符串或 None"""
    client = _get_redis()
    if client is None:
        return None
    try:
        return client.get(key)
    except Exception as e:
        logger.warning("Redis GET 失败 (key=%s): %s", key, e)
        return None


def cache_get_json(key):
    """读取缓存并反序列化为 Python 对象"""
    val = cache_get(key)
    if val is None:
        return None
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return None


def cache_set(key, value, ttl=300):
    """写入缓存（字符串值），ttl 单位秒"""
    client = _get_redis()
    if client is None:
        return
    try:
        client.setex(key, ttl, value)
    except Exception as e:
        logger.warning("Redis SET 失败 (key=%s): %s", key, e)


def cache_set_json(key, value, ttl=300):
    """写入缓存（JSON 序列化），ttl 单位秒"""
    try:
        cache_set(key, json.dumps(value, ensure_ascii=False, default=str), ttl)
    except Exception as e:
        logger.warning("Redis SET JSON 失败 (key=%s): %s", key, e)


def cache_delete(key):
    """删除缓存键"""
    client = _get_redis()
    if client is None:
        return
    try:
        client.delete(key)
    except Exception as e:
        logger.warning("Redis DELETE 失败 (key=%s): %s", key, e)


def cache_delete_pattern(pattern):
    """按模式删除缓存键（如 user_perms:*）"""
    client = _get_redis()
    if client is None:
        return
    try:
        keys = client.keys(pattern)
        if keys:
            client.delete(*keys)
    except Exception as e:
        logger.warning("Redis DELETE PATTERN 失败 (pattern=%s): %s", pattern, e)


# ===== 业务缓存场景 =====

# 缓存键前缀与 TTL
KEY_USER_PERMS = 'user_perms:{user_id}'
TTL_USER_PERMS = 300       # 5 分钟
KEY_SYS_CONFIG = 'sys_config'
TTL_SYS_CONFIG = 600       # 10 分钟
KEY_DASHBOARD_STATS = 'dashboard_stats:{project_key}'
TTL_DASHBOARD_STATS = 120  # 2 分钟


def get_user_perms_cache(user_id):
    """读取用户权限缓存"""
    return cache_get_json(KEY_USER_PERMS.format(user_id=user_id))


def set_user_perms_cache(user_id, perms_set, ttl=TTL_USER_PERMS):
    """写入用户权限缓存（perms_set 为 list）"""
    cache_set_json(KEY_USER_PERMS.format(user_id=user_id), perms_set, ttl)


def invalidate_user_perms_cache(user_id):
    """失效用户权限缓存（用户角色/权限变更时调用）"""
    cache_delete(KEY_USER_PERMS.format(user_id=user_id))


def get_sys_config_cache():
    """读取系统配置缓存"""
    return cache_get_json(KEY_SYS_CONFIG)


def set_sys_config_cache(config_dict, ttl=TTL_SYS_CONFIG):
    """写入系统配置缓存"""
    cache_set_json(KEY_SYS_CONFIG, config_dict, ttl)


def invalidate_sys_config_cache():
    """失效系统配置缓存"""
    cache_delete(KEY_SYS_CONFIG)


def get_dashboard_stats_cache(project_key):
    """读取 Dashboard 统计缓存"""
    return cache_get_json(KEY_DASHBOARD_STATS.format(project_key=project_key))


def set_dashboard_stats_cache(project_key, stats_dict, ttl=TTL_DASHBOARD_STATS):
    """写入 Dashboard 统计缓存"""
    cache_set_json(KEY_DASHBOARD_STATS.format(project_key=project_key), stats_dict, ttl)


def invalidate_dashboard_stats_cache():
    """失效所有 Dashboard 统计缓存"""
    cache_delete_pattern('dashboard_stats:*')


def is_redis_available():
    """检查 Redis 是否可用（用于健康检查）"""
    return _get_redis() is not None
