import logging
import os
import sys
import signal
import time
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

# ---------------- LOGGING SETUP ---------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# Re-create credentials.json from environment variable for Google libraries
try:
    google_creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    if google_creds_json:
        logger.info("Writing credentials.json from environment variable")
        with open("credentials.json", "w") as f:
            f.write(google_creds_json)
        logger.info("credentials.json written successfully")
    else:
        logger.warning("GOOGLE_CREDENTIALS_JSON environment variable not set")
except Exception as e:
    logger.error(f"Failed to write credentials.json: {e}")
    # Don't fail startup, let individual tool calls handle missing credentials

# ---------------- APP INIT ---------------- #
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Google MCP Server lifespan startup")
    global keep_alive, keep_thread
    keep_alive = True
    keep_thread = threading.Thread(target=keep_alive_thread, daemon=True)
    keep_thread.start()
    logger.info("Keep-alive thread started in lifespan")
    yield
    # Shutdown
    logger.info("Google MCP Server lifespan shutdown")
    global keep_alive
    keep_alive = False
    logger.info("Keep-alive thread stopped in lifespan")

app = FastAPI(
    title="Google MCP Server",
    lifespan=lifespan,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ]
)

# Request logging middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    logger.info(f"Incoming request: {request.method} {request.url}")
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    logger.info(f"Request completed: {request.method} {request.url} - Status: {response.status_code} - Duration: {duration:.2f}s")
    return response

# Check credentials at startup
logger.info("Google MCP Server is starting up...")
logger.info(f"Python version: {sys.version}")
logger.info(f"Working directory: {os.getcwd()}")
logger.info(f"Files in current directory: {os.listdir('.')}")
logger.info(f"Environment PORT: {os.environ.get('PORT', 'not set')}")
logger.info(f"Environment RAILWAY_ENVIRONMENT: {os.environ.get('RAILWAY_ENVIRONMENT', 'not set')}")

if not os.environ.get("GOOGLE_CREDENTIALS_JSON"):
    logger.warning("GOOGLE_CREDENTIALS_JSON env var not set - server may fail on API calls")
if not os.environ.get("GOOGLE_TOKEN_JSON"):
    logger.warning("GOOGLE_TOKEN_JSON env var not set - server may fail on API calls")

# Signal handlers for debugging
def handle_signal(signum, frame):
    logger.info(f"Received signal {signum}, frame: {frame}")
    logger.info("Signal handler called - app is being terminated")
    global keep_alive
    keep_alive = False
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)
logger.info("Signal handlers registered")

# Background keep-alive thread to prevent Railway inactivity detection
keep_alive = True
keep_thread = None

def keep_alive_thread():
    """Background thread to keep container active and prevent Railway from killing it"""
    global keep_alive
    logger.info("Keep-alive thread started")
    while keep_alive:
        time.sleep(30)  # Log every 30 seconds
        logger.info("Keep-alive heartbeat - container is active")
    logger.info("Keep-alive thread stopped")


# ---------------- REQUEST SCHEMAS ---------------- #
class AppendDocInput(BaseModel):
    doc_id: str
    content: str


class EmailInput(BaseModel):
    to: str 
    subject: str
    body: str


# ---------------- APPROVAL LAYER ---------------- #
def approve(action: str, payload: dict) -> bool:
    """
    Approval system:
    - Local → manual approval
    - Deployment → auto-approved
    """

    # ✅ Auto-approve in deployment (set AUTO_APPROVE=true in Render/Railway/etc.)
    if (
        os.getenv("AUTO_APPROVE", "false").lower() == "true"
        or os.getenv("RENDER")
        or os.getenv("RAILWAY_ENVIRONMENT")
    ):
        logger.info(f"{action} auto-approved (deployment env)")
        return True

    # 🧪 Local CLI approval
    try:
        print("\n-----------------------------")
        print(f"ACTION: {action}")
        print(f"PAYLOAD: {payload}")
        print("-----------------------------")

        decision = input("Approve? (y/n): ").strip().lower()

        if decision == "y":
            logger.info(f"{action} approved")
            return True
        else:
            logger.warning(f"{action} rejected")
            return False

    except Exception as e:
        logger.error(f"Approval error: {e}")
        return False


# ---------------- MCP TOOL LIST ---------------- #
@app.get("/tools")
def list_tools():
    return [
        {
            "name": "append_to_doc",
            "description": "Append content to Google Doc"
        },
        {
            "name": "create_email_draft",
            "description": "Create Gmail draft"
        }
    ]


# ---------------- DOC TOOL ---------------- #
@app.post("/append_to_doc")
def run_append(data: AppendDocInput):
    try:
        from docs_tool import append_to_doc
        logger.info("Received request for append_to_doc")

        if not approve("append_to_doc", data.dict()):
            return {
                "status": "rejected",
                "message": "User rejected the action"
            }

        result = append_to_doc(
            doc_id=data.doc_id,
            content=data.content
        )

        logger.info("append_to_doc executed successfully")

        return result

    except Exception as e:
        logger.error(f"Error in append_to_doc: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ---------------- EMAIL TOOL ---------------- #
@app.post("/create_email_draft")
def run_email(data: EmailInput):
    try:
        from gmail_tool import create_email_draft
        logger.info("Received request for create_email_draft")

        if not approve("create_email_draft", data.dict()):
            return {
                "status": "rejected",
                "message": "User rejected the action"
            }

        result = create_email_draft(
            to=data.to,
            subject=data.subject,
            body=data.body
        )

        logger.info("create_email_draft executed successfully")

        return result

    except Exception as e:
        logger.error(f"Error in create_email_draft: {e}")
        raise HTTPException(
            status_code=500,
            detail=str(e)
        )


# ---------------- HEALTH CHECK ---------------- #
@app.get("/")
def root():
    return {
        "message": "Google MCP Server is running 🚀",
        "timestamp": time.time(),
        "status": "healthy"
    }

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "uptime": "active"
    }

@app.get("/ping")
def ping():
    return {"pong": True, "timestamp": time.time()}