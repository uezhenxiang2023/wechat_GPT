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
GPT4_MULTIMODEL_LIST = [GPT4_TURBO,GPT4_OMNI]
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
GEMINI_1_PRO_LIST = [GEMINI_1_PRO_LATEST_VERSION, GEMINI_1_LATEST_STABLE_VERSION, GEMINI_1_STABLE_VERSION]
GEMINI_15_PRO_LIST = [GEMINI_15_PRO_LATEST_VERSION, GEMINI_15_LATEST_STABLE_VERSION, GEMINI_15_STABLE_VERSION]
GEMINI_15_FLASH_LIST = [GEMINI_15_FLASH_LATEST_VERSION, GEMINI_15_FLASH_LATEST_STABLE_VERSION, GEMINI_15_FLASH_STABLE_VERSION]

# switch model in cmd
MODEL_LIST = [
    GPT35,
    GPT4, 
    "wenxin", 
    "wenxin-4", 
    "xunfei",
    GPT4_OMNI,  
    GPT4_TURBO, 
    GPT4_TURBO_PREVIEW,
    GPT4_VISION_PREVIEW, 
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
    GEMINI_15_FLASH_LATEST_VERSION
    ]

# channel
FEISHU = "feishu"
DINGTALK = "dingtalk"   

# media files type
IMAGE = ['png', 'jpeg', 'webp', 'heic', 'heif']
AUDIO = ['wav', 'mp3', 'aiff', 'aac', 'ogg', 'flac']
VIDEO = ['mp4', 'mpeg', 'mov', 'avi', 'x-flv', 'mpg', 'webm', 'wmv', '3gpp']
PDF = 'pdf'
DOCUMENT = ['rtf', 'markdown']
SPREADSHEET = ['csv', 'xml']
PRESENTATION = ['ppt', 'pptx']