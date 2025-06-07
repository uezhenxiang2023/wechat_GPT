# bot_type
OPEN_AI = "openAI"
CHATGPT = "chatGPT"
BAIDU = "baidu"
XUNFEI = "xunfei"
CHATGPTONAZURE = "chatGPTOnAzure"
LINKAI = "linkai"
CLAUDEAI = "claude"
QWEN = "qwen"
GEMINI = "gemini"
OPEN_AI_ASSISTANT = 'OpenAIAssistant'

# model
GPT35 = "gpt-3.5-turbo"
GPT4 = "gpt-4"
GPT4_TURBO_PREVIEW = "gpt-4-turbo-preview"
GPT4_VISION_PREVIEW = "gpt-4-vision-preview"
GPT4_TURBO = 'gpt-4-turbo'
GPT4_OMNI = 'gpt-4o'
GPT4_OMNI_MINI = 'gpt-4o-mini'
GPT4_MULTIMODEL_LIST = [GPT4_TURBO, GPT4_OMNI, GPT4_OMNI_MINI]
O1_PREVIEW = "o1-preview"
O1_MINI = "o1-mini"
WHISPER_1 = "whisper-1"
TTS_1 = "tts-1"
TTS_1_HD = "tts-1-hd"
CLAUDE_3_HAIKU = "claude-3-haiku-20240307"
CLAUDE_3_SONNET = "claude-3-sonnet-20240229"
CLAUDE_3_OPUS = "claude-3-opus-20240229"
CLAUDE_35_SONNET = "claude-3-5-sonnet-20240620"
CLAUDE_21 = "claude-2.1"
CLAUDE_20 = "claude-2.0"
CLAUDE_2_LIST = [CLAUDE_20, CLAUDE_21]
CLAUDE_3_LIST = [CLAUDE_3_HAIKU, CLAUDE_3_SONNET, CLAUDE_3_OPUS]
CLAUDE_35_LIST = [CLAUDE_35_SONNET]
GEMINI_1_PRO_LATEST_VERSION = "gemini-1.0-pro-latest"
GEMINI_1_LATEST_STABLE_VERSION = "gemini-1.0-pro"
GEMINI_1_STABLE_VERSION = "gemini-1.0-pro-001"
GEMINI_1_PRO_VISION_LATEST = 'gemini-1.0-pro-vision-latest'
GEMINI_1_PRO_VISION_STABLE = 'gemini-1.0-pro-vision'
GEMINI_15_PRO_LATEST_VERSION = "gemini-1.5-pro-latest"
GEMINI_15_LATEST_STABLE_VERSION = "gemini-1.5-pro"
GEMINI_15_STABLE_VERSION = "gemini-1.5-pro-001"
GEMINI_15_FLASH_LATEST_VERSION = "gemini-1.5-flash-latest"
GEMINI_15_FLASH_LATEST_STABLE_VERSION = "gemini-1.5-flash"
GEMINI_15_FLASH_STABLE_VERSION = "gemini-1.5-flash-001"
GEMINI_2_FLASH_EXP = "gemini-2.0-flash-exp"
GEMINI_2_FLASH_THINK_EXP = "gemini-2.0-flash-think-exp"
GEMINI_2_FLASH = "gemini-2.0-flash"
GEMINI_2_FLASH_LITE = "gemini-2.0-flash-lite"
GEMINI_2_FLASH_IMAGE_GENERATION = 'gemini-2.0-flash-preview-image-generation'
GEMINI_2_PRO_EXP = "gemini-2.0-pro-exp-02-05"
GEMINI_25_PRO_EXP = "gemini-2.5-pro-exp-03-25"
GEMINI_25_PRO_PREVRIEW = "gemini-2.5-pro-preview-06-05"
GEMINI_25_FLASH_PREVIEW = "gemini-2.5-flash-preview-04-17"
GEMINI_1_PRO_LIST = [GEMINI_1_PRO_LATEST_VERSION, GEMINI_1_LATEST_STABLE_VERSION, GEMINI_1_STABLE_VERSION]
GEMINI_15_PRO_LIST = [GEMINI_15_PRO_LATEST_VERSION, GEMINI_15_LATEST_STABLE_VERSION, GEMINI_15_STABLE_VERSION]
GEMINI_15_FLASH_LIST = [GEMINI_15_FLASH_LATEST_VERSION, GEMINI_15_FLASH_LATEST_STABLE_VERSION, GEMINI_15_FLASH_STABLE_VERSION]
GEMINI_2_FLASH_LIST = [GEMINI_2_FLASH_EXP, GEMINI_2_FLASH_THINK_EXP, GEMINI_2_FLASH, GEMINI_2_PRO_EXP, GEMINI_2_FLASH_LITE]
GEMINI_25_PRO_LIST = [GEMINI_25_PRO_PREVRIEW, GEMINI_25_PRO_EXP, GEMINI_25_FLASH_PREVIEW]
GEMINI_GENAI_SDK = [GEMINI_2_FLASH, GEMINI_2_FLASH_LITE, GEMINI_2_FLASH_IMAGE_GENERATION, GEMINI_2_PRO_EXP, GEMINI_25_PRO_PREVRIEW, GEMINI_25_PRO_EXP, GEMINI_25_FLASH_PREVIEW]

# switch model in cmd
MODEL_LIST = [
    GPT35,
    GPT4,
    "wenxin",
    "wenxin-4",
    "xunfei",
    GPT4_OMNI,
    GPT4_OMNI_MINI,
    GPT4_TURBO,
    GPT4_TURBO_PREVIEW,
    GPT4_VISION_PREVIEW,
    O1_PREVIEW,
    O1_MINI,
    QWEN,
    GEMINI,
    OPEN_AI_ASSISTANT,
    CLAUDE_3_HAIKU,
    CLAUDE_3_SONNET,
    CLAUDE_3_OPUS,
    CLAUDE_35_SONNET,
    CLAUDE_21,
    CLAUDE_20,
    GEMINI_1_PRO_LATEST_VERSION,
    GEMINI_15_PRO_LATEST_VERSION,
    GEMINI_15_FLASH_LATEST_VERSION,
    GEMINI_15_FLASH_LATEST_STABLE_VERSION,
    GEMINI_2_FLASH_EXP,
    GEMINI_2_FLASH_THINK_EXP,
    GEMINI_2_FLASH,
    GEMINI_2_PRO_EXP,
    GEMINI_2_FLASH_LITE,
    GEMINI_25_PRO_EXP,
    GEMINI_25_FLASH_PREVIEW,
    ]

# channel
FEISHU = "feishu"
DINGTALK = "dingtalk"
TELEGRAM = "telegram"

# media files type
IMAGE = ['png', 'jpg', 'jpeg', 'webp', 'heic', 'heif']
AUDIO = ['wav', 'mp3', 'aiff', 'aac', 'ogg', 'flac']
VIDEO = ['mp4', 'mpeg', 'mov', 'avi', 'x-flv', 'mpg', 'webm', 'wmv', '3gpp']
TXT = ['rtf', 'md', 'css', 'html', 'plain', 'javascript']
DOCUMENT = ['pdf', 'doc', 'docx', 'plain']
SPREADSHEET = ['csv', 'xml']
PRESENTATION = ['ppt', 'pptx']
APPLICATION = ['x-javascript', 'x-python']

# custom service reply
ERROR_RESPONSE = "网络有点小烦忙，请过几秒再试一试，给您带来不便，大超子深表歉意"
