"""Loader konfigurasi terpusat untuk bridge."""
import os
import yaml
from dotenv import load_dotenv

_DIR = os.path.dirname(os.path.abspath(__file__))


def load_config(path=None):
    path = path or os.path.join(_DIR, "config.yaml")
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    load_dotenv(os.path.join(_DIR, ".env"))

    # === Seluruh konfigurasi LLM dari .env (env override config.yaml) ===
    llm_cfg = cfg.get("llm", {})
    def env(key, default, cast=str, is_bool=False):
        v = os.environ.get(key)
        if v is None or v == "":
            return default
        try:
            return cast(v)
        except ValueError:
            return default

    llm_cfg["api_key"] = env(llm_cfg.get("api_key_env", "LLM_API_KEY"), "")
    llm_cfg["base_url"] = env(llm_cfg.get("base_url_env", "LLM_BASE_URL"), "")
    llm_cfg["model"] = env("LLM_MODEL", llm_cfg.get("model", ""))
    llm_cfg["temperature"] = env("LLM_TEMPERATURE", llm_cfg.get("temperature", 0.2), float)
    llm_cfg["max_tokens"] = env("LLM_MAX_TOKENS", llm_cfg.get("max_tokens", 600), int)
    llm_cfg["timeout_s"] = env("LLM_TIMEOUT_S", llm_cfg.get("timeout_s", 15), int)
    llm_cfg["json_mode"] = env("LLM_JSON_MODE", llm_cfg.get("json_mode", True),
                               lambda s: str(s).lower() in ("1", "true", "yes"))
    llm_cfg["retries"] = env("LLM_RETRIES", llm_cfg.get("retries", 2), int)
    llm_cfg["fallback_after_failures"] = env(
        "LLM_FALLBACK_AFTER_FAILURES", llm_cfg.get("fallback_after_failures", 3), int)
    llm_cfg["min_llm_confidence"] = env("LLM_MIN_CONFIDENCE",
                                        llm_cfg.get("min_llm_confidence", 0.55), float)
    llm_cfg["ready"] = bool(llm_cfg["api_key"] and llm_cfg["base_url"]
                            and "<" not in llm_cfg["base_url"])
    cfg["llm"] = llm_cfg
    bridge = cfg.setdefault("bridge", {})
    bridge["token"] = os.environ.get("BRIDGE_TOKEN", bridge.get("token", ""))
    bridge["require_token"] = os.environ.get(
        "BRIDGE_REQUIRE_TOKEN", str(bridge.get("require_token", False))
    ).lower() in ("1", "true", "yes")
    cfg["server"]["host"] = os.environ.get("BRIDGE_HOST", cfg["server"].get("host", "127.0.0.1"))
    cfg["server"]["port"] = int(os.environ.get("BRIDGE_PORT", cfg["server"].get("port", 8080)))
    return cfg


def resolve_path(cfg, key):
    return os.path.join(_DIR, cfg.get(key, {}).get("db_path", "data/memory.db"))


def ensure_data_dir(cfg):
    for key in ("db_path", "file"):
        p = cfg.get("memory", {}).get(key)
        if p:
            os.makedirs(os.path.dirname(os.path.join(_DIR, p)), exist_ok=True)
