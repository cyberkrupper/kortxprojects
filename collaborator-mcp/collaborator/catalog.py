"""Model catalog: providers, model specs, pricing and cost math.

The built-in list below is a *seed*. Model line-ups move fast, so the app can
also discover models straight from each provider's /models endpoint and store
them alongside these (see `load_custom` and `engine.refresh_models`).

Prices are USD per 1M tokens. First-party Anthropic prices are authoritative;
third-party prices are best-effort and flagged `price_estimated=True`, so the
UI can say so rather than quietly implying precision it does not have.
"""

from dataclasses import dataclass, field


# --- provider ids -----------------------------------------------------------
ANTHROPIC = "anthropic"
OPENAI = "openai"
GOOGLE = "google"
MOONSHOT = "moonshot"
QWEN = "qwen"
# Subscription-backed local CLIs (Claude Pro/Max, ChatGPT Plus/Pro).
CLI = "cli"


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    label: str
    key_setting: str          # settings key holding the API key
    env_var: str              # environment fallback
    base_url: str             # OpenAI-compatible base, "" for non-compatible
    base_url_setting: str = ""   # optional settings override
    openai_compatible: bool = True
    signup: str = ""


PROVIDERS = {
    ANTHROPIC: ProviderInfo(
        ANTHROPIC, "Anthropic", "anthropic_api_key", "ANTHROPIC_API_KEY",
        "https://api.anthropic.com/v1", openai_compatible=False,
        signup="https://platform.claude.com"),
    OPENAI: ProviderInfo(
        OPENAI, "OpenAI", "openai_api_key", "OPENAI_API_KEY",
        "https://api.openai.com/v1", "openai_base_url",
        signup="https://platform.openai.com"),
    GOOGLE: ProviderInfo(
        GOOGLE, "Google Gemini", "google_api_key", "GEMINI_API_KEY",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "google_base_url", signup="https://aistudio.google.com/apikey"),
    MOONSHOT: ProviderInfo(
        MOONSHOT, "Moonshot (Kimi)", "moonshot_api_key", "MOONSHOT_API_KEY",
        "https://api.moonshot.ai/v1", "moonshot_base_url",
        signup="https://platform.moonshot.ai"),
    QWEN: ProviderInfo(
        QWEN, "Alibaba (Qwen)", "qwen_api_key", "DASHSCOPE_API_KEY",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "qwen_base_url", signup="https://modelstudio.console.alibabacloud.com"),
    CLI: ProviderInfo(
        CLI, "Local subscription CLI", "", "", "", openai_compatible=False),
}

API_PROVIDERS = [ANTHROPIC, OPENAI, GOOGLE, MOONSHOT, QWEN]


@dataclass(frozen=True)
class ModelSpec:
    id: str
    provider: str
    label: str
    input_price: float      # USD / 1M input tokens
    output_price: float     # USD / 1M output tokens
    context: int
    tier: str               # "frontier" | "mid" | "small"
    notes: str = ""
    # Anthropic 4.6+ family rejects temperature/top_p/top_k and budget_tokens.
    no_sampling_params: bool = False
    supports_effort: bool = False
    # Adaptive thinking cannot be switched off on these; never send
    # thinking={"type": "disabled"} - the API rejects it.
    thinking_always_on: bool = False
    default_effort: str = ""
    # True when the price came from a secondary source rather than the vendor.
    price_estimated: bool = False
    discovered: bool = False     # came from a provider /models call
    tags: tuple = field(default_factory=tuple)
    # CLI-backed models only:
    cli_bin: str = ""
    cli_model: str = ""
    cli_kind: str = ""

    @property
    def subscription(self):
        return self.provider == CLI

    @property
    def provider_label(self):
        info = PROVIDERS.get(self.provider)
        return info.label if info else self.provider


SEED = [
    # --- Anthropic ----------------------------------------------------------
    ModelSpec("claude-opus-5-5", ANTHROPIC, "Claude Opus 5.5", 4.00, 20.00,
              1_000_000, "frontier",
              "Current default. Long-running agentic coding and knowledge "
              "work. Cheaper than Opus 5.",
              no_sampling_params=True, supports_effort=True,
              thinking_always_on=True, default_effort="medium",
              tags=("executor", "orchestrator")),
    ModelSpec("claude-fable-5-1", ANTHROPIC, "Claude Fable 5.1", 10.00, 50.00,
              1_000_000, "frontier",
              "Most capable. Demanding reasoning and long-horizon agentic "
              "work.",
              no_sampling_params=True, supports_effort=True,
              thinking_always_on=True, default_effort="high",
              tags=("executor", "orchestrator")),
    ModelSpec("claude-opus-5", ANTHROPIC, "Claude Opus 5", 5.00, 25.00,
              1_000_000, "frontier", "Previous Opus generation.",
              no_sampling_params=True, supports_effort=True,
              default_effort="high",
              tags=("executor", "orchestrator", "fallback")),
    ModelSpec("claude-opus-4-8", ANTHROPIC, "Claude Opus 4.8", 5.00, 25.00,
              1_000_000, "frontier", "Legacy Opus. Useful refusal fallback.",
              no_sampling_params=True, supports_effort=True,
              tags=("executor", "orchestrator", "fallback")),
    ModelSpec("claude-sonnet-5", ANTHROPIC, "Claude Sonnet 5", 2.00, 10.00,
              1_000_000, "mid", "Best speed-to-intelligence balance.",
              no_sampling_params=True, supports_effort=True,
              default_effort="high", tags=("executor", "agent")),
    ModelSpec("claude-haiku-4-5", ANTHROPIC, "Claude Haiku 4.5", 1.00, 5.00,
              200_000, "small", "Fast and cheap. Good sub-agent fan-out.",
              tags=("agent",)),

    # --- OpenAI -------------------------------------------------------------
    ModelSpec("gpt-4.1-mini", OPENAI, "GPT-4.1 mini", 0.40, 1.60, 1_047_576,
              "small", "Cheap general-purpose sub-agent.", tags=("agent",)),
    ModelSpec("gpt-4.1-nano", OPENAI, "GPT-4.1 nano", 0.10, 0.40, 1_047_576,
              "small", "Cheapest OpenAI sub-agent.", tags=("agent",)),
    ModelSpec("gpt-4o-mini", OPENAI, "GPT-4o mini", 0.15, 0.60, 128_000,
              "small", "Widely available small model.", tags=("agent",)),
    ModelSpec("o4-mini", OPENAI, "o4-mini", 1.10, 4.40, 200_000,
              "small", "Reasoning-tuned small model.", tags=("agent",)),

    # --- Google Gemini ------------------------------------------------------
    ModelSpec("gemini-3.8-flash", GOOGLE, "Gemini 3.8 Flash", 0.75, 3.75,
              1_000_000, "frontier",
              "Google's current flagship Flash. Introductory pricing to "
              "2026-12-31; $1.50/$7.50 after.",
              price_estimated=True, tags=("executor", "orchestrator", "agent")),
    ModelSpec("gemini-3.1-pro-preview", GOOGLE, "Gemini 3.1 Pro (preview)",
              0.0, 0.0, 1_000_000, "frontier",
              "Pro tier, preview. Pricing not published here - check Google.",
              price_estimated=True, tags=("executor", "orchestrator")),
    ModelSpec("gemini-3.7-flash", GOOGLE, "Gemini 3.7 Flash", 0.75, 3.75,
              1_000_000, "mid", "Previous Flash generation.",
              price_estimated=True, tags=("executor", "agent")),
    ModelSpec("gemini-3.1-flash-lite", GOOGLE, "Gemini 3.1 Flash-Lite",
              0.10, 0.40, 1_000_000, "small",
              "Most cost-efficient Gemini, tuned for low latency.",
              price_estimated=True, tags=("agent",)),

    # --- Moonshot / Kimi ----------------------------------------------------
    ModelSpec("kimi-k3", MOONSHOT, "Kimi K3", 0.95, 4.00, 1_000_000,
              "frontier",
              "Moonshot's most capable: 2.8T params, native vision, 1M "
              "context.",
              price_estimated=True, tags=("executor", "orchestrator", "agent")),
    ModelSpec("kimi-k2.7-code", MOONSHOT, "Kimi K2.7 Code", 0.95, 4.00,
              256_000, "mid", "Coding-tuned.",
              price_estimated=True, tags=("executor", "agent")),
    ModelSpec("kimi-k2.7-code-highspeed", MOONSHOT, "Kimi K2.7 Code (high speed)",
              0.95, 4.00, 256_000, "mid", "~180-260 tokens/s output.",
              price_estimated=True, tags=("agent",)),
    ModelSpec("kimi-k2.6", MOONSHOT, "Kimi K2.6", 0.95, 4.00, 256_000,
              "mid", "Previous generation.",
              price_estimated=True, tags=("agent",)),

    # --- Alibaba / Qwen -----------------------------------------------------
    ModelSpec("qwen3.8-max", QWEN, "Qwen3.8 Max", 2.00, 6.00, 1_000_000,
              "frontier", "Qwen flagship.",
              price_estimated=True, tags=("executor", "orchestrator", "agent")),
    ModelSpec("qwen3.7-plus", QWEN, "Qwen3.7 Plus", 0.40, 1.60, 1_000_000,
              "mid", "Price-performance tier.",
              price_estimated=True, tags=("executor", "agent")),
    ModelSpec("qwen3.8-flash", QWEN, "Qwen3.8 Flash", 0.15, 0.47, 1_000_000,
              "small", "Cheap tier of the current generation.",
              price_estimated=True, tags=("agent",)),

    # --- Subscription CLIs (no API key, no per-token charge) ----------------
    ModelSpec("claude-cli", CLI, "Claude Code (Pro/Max plan)", 0.0, 0.0,
              1_000_000, "frontier",
              "Runs through your local Claude Code login. Uses plan quota, "
              "not API credit.",
              tags=("executor", "orchestrator", "agent"),
              cli_bin="claude", cli_model="", cli_kind="claude"),
    ModelSpec("claude-cli-sonnet", CLI, "Claude Code - Sonnet (Pro/Max)",
              0.0, 0.0, 1_000_000, "mid",
              "Claude Code pinned to Sonnet. Lighter on plan quota.",
              tags=("executor", "agent"),
              cli_bin="claude", cli_model="sonnet", cli_kind="claude"),
    ModelSpec("claude-cli-haiku", CLI, "Claude Code - Haiku (Pro/Max)",
              0.0, 0.0, 200_000, "small",
              "Claude Code pinned to Haiku. Cheapest on plan quota.",
              tags=("agent",),
              cli_bin="claude", cli_model="haiku", cli_kind="claude"),
    ModelSpec("codex-cli", CLI, "Codex CLI (ChatGPT Plus/Pro)", 0.0, 0.0,
              400_000, "frontier",
              "Runs through your local Codex login. Uses ChatGPT plan quota.",
              tags=("executor", "orchestrator", "agent"),
              cli_bin="codex", cli_model="", cli_kind="codex"),
]

# Models discovered from provider /models endpoints or added by the user.
_CUSTOM = []


def load_custom(settings):
    """Rebuild the discovered-model list from saved settings."""
    global _CUSTOM
    out = []
    for row in (settings.get("custom_models") or []):
        if not isinstance(row, dict) or not row.get("id"):
            continue
        if any(m.id == row["id"] for m in SEED):
            continue          # a seed entry already covers it
        out.append(ModelSpec(
            id=row["id"],
            provider=row.get("provider") or OPENAI,
            label=row.get("label") or row["id"],
            input_price=float(row.get("input_price") or 0.0),
            output_price=float(row.get("output_price") or 0.0),
            context=int(row.get("context") or 0),
            tier=row.get("tier") or "mid",
            notes=row.get("notes") or "Discovered from the provider.",
            price_estimated=bool(row.get("price_estimated", True)),
            discovered=True,
            tags=tuple(row.get("tags") or ("executor", "agent")),
        ))
    _CUSTOM = out
    _rebuild_index()
    return out


CATALOG = []
BY_ID = {}


def _rebuild_index():
    global CATALOG, BY_ID
    CATALOG = list(SEED) + list(_CUSTOM)
    BY_ID = {m.id: m for m in CATALOG}


_rebuild_index()


# --- lookups ----------------------------------------------------------------

def get(model_id):
    return BY_ID.get(model_id)


def agent_models():
    return [m for m in CATALOG if "agent" in m.tags]


def executor_models():
    return [m for m in CATALOG if "executor" in m.tags]


def orchestrator_models():
    return [m for m in CATALOG if "orchestrator" in m.tags]


def subscription_models():
    return [m for m in CATALOG if m.provider == CLI]


def api_models():
    return [m for m in CATALOG if m.provider != CLI]


def by_provider(provider):
    return [m for m in CATALOG if m.provider == provider]


def is_subscription(model_id):
    spec = BY_ID.get(model_id)
    return bool(spec and spec.provider == CLI)


def provider_of(model_id):
    spec = BY_ID.get(model_id)
    if spec:
        return spec.provider
    if model_id.startswith(("gpt", "o1", "o3", "o4", "chatgpt")):
        return OPENAI
    if model_id.startswith("gemini"):
        return GOOGLE
    if model_id.startswith("kimi") or model_id.startswith("moonshot"):
        return MOONSHOT
    if model_id.startswith("qwen"):
        return QWEN
    return ANTHROPIC


def cli_available(model_id):
    """True when the CLI backing this model is on PATH."""
    from .cli import find_cli
    spec = BY_ID.get(model_id)
    if not spec or not spec.cli_bin:
        return False
    return find_cli(spec.cli_bin) is not None


# --- cost -------------------------------------------------------------------

def cost(model_id, input_tokens, output_tokens):
    """USD cost of a call. Subscription models cost nothing extra."""
    spec = BY_ID.get(model_id)
    if not spec or spec.provider == CLI:
        return 0.0
    return (input_tokens / 1_000_000.0) * spec.input_price + \
           (output_tokens / 1_000_000.0) * spec.output_price


def fmt_cost(usd):
    if usd >= 1:
        return "${:.2f}".format(usd)
    if usd >= 0.01:
        return "${:.3f}".format(usd)
    return "${:.5f}".format(usd)


def price_label(spec):
    """Human-readable billing string for a model."""
    if spec.subscription:
        return "plan quota (no API charge)"
    if not spec.input_price and not spec.output_price:
        return "price unknown"
    text = "$%.2f in / $%.2f out per Mtok" % (spec.input_price,
                                              spec.output_price)
    return text + (" (approx)" if spec.price_estimated else "")
