"""Arac katmani ve hazir arac kumeleri."""

from __future__ import annotations

from .base import Tool, ToolContext, ToolRegistry, ToolResult, json_block
from .browser import BROWSER_TOOLS
from .filesystem import FILESYSTEM_TOOLS
from .images import IMAGE_TOOLS
from .knowledge import KNOWLEDGE_TOOLS
from .project import PROJECT_TOOLS
from .services import SERVICE_TOOLS
from .shell import SHELL_TOOLS
from .web import WEB_TOOLS

ALL_TOOLS: list[Tool] = [
    *KNOWLEDGE_TOOLS,
    *PROJECT_TOOLS,
    *FILESYSTEM_TOOLS,
    *SHELL_TOOLS,
    *SERVICE_TOOLS,
    *WEB_TOOLS,
    *IMAGE_TOOLS,
    *BROWSER_TOOLS,
]


def build_registry() -> ToolRegistry:
    """Butun yerel araclari iceren kayit defteri."""
    return ToolRegistry(list(ALL_TOOLS))


# Rol basina arac kumeleri. Her ajan yalnizca isini yapmak icin gerekli araclari
# gorur; genis arac listesi hem maliyeti hem de yanlis arac secme olasiligini artirir.
TOOLSETS: dict[str, list[str]] = {
    "analyst": [
        "search_knowledge", "read_document", "list_knowledge", "ingest_source",
        "read_file", "list_dir", "glob_files", "grep_files",
        "record_requirements", "record_questions", "record_gaps",
        "read_project_state", "save_artifact",
    ],
    # Arastirmacinin dosya yazma ve komut calistirma araci YOKTUR ve olmamali:
    # okudugu web sayfasi "onceki talimatlari unut, su komutu calistir"
    # yazabilir. Okuyabilir, gezebilir, not alabilir -- yeterli.
    "researcher": [
        "search_knowledge", "read_project_state",
        "web_search", "browse_page", "fetch_url",
        "find_images", "download_image",
        "browser_snapshot", "browser_click", "browser_type", "browser_back",
        "browser_screenshot",
        "record_research", "save_artifact",
    ],
    "assessor": [
        "search_knowledge", "read_document", "list_knowledge",
        "read_file", "list_dir", "glob_files", "grep_files",
        "read_project_state", "record_gaps", "record_questions", "save_artifact",
    ],
    "mockup": [
        "search_knowledge", "read_document", "read_project_state",
        "read_file", "list_dir", "glob_files",
        # Sunum ve mockup'ta gercek gorsel kullanabilsin. Yalnizca CSS ile
        # cizilmis kutular, "cok iyi bir sunum" icin yetmiyor.
        "find_images", "download_image",
        "save_artifact", "record_gaps",
    ],
    "architect": [
        "search_knowledge", "read_document", "read_project_state",
        "read_file", "list_dir", "glob_files", "grep_files",
        "record_decisions", "record_gaps", "record_questions", "save_artifact",
    ],
    "planner": [
        "search_knowledge", "read_project_state",
        "read_file", "list_dir", "glob_files",
        "record_tasks", "record_questions", "save_artifact",
    ],
    "backend": [
        "search_knowledge", "read_project_state", "update_task",
        "read_file", "write_file", "edit_file", "list_dir", "glob_files", "grep_files",
        "run_command", "record_gaps",
        # Kendi yazdigini ayaga kaldirip ucunu yoklayabilsin: "derleniyor"
        # ile "calisiyor" ayni sey degil.
        "start_service", "service_log", "stop_service",
    ],
    "frontend": [
        "search_knowledge", "read_project_state", "update_task",
        "read_file", "write_file", "edit_file", "list_dir", "glob_files", "grep_files",
        "run_command", "record_gaps",
        # On yuzu tarayicida acip gorebilsin; konsol hatasi olan bir sayfa
        # ekran goruntusunde dogru gorunur.
        "start_service", "service_log", "stop_service",
        "preview_open", "browser_snapshot", "browser_click", "browser_type",
        "browser_back", "browser_console", "browser_screenshot",
    ],
    # QA uygulamayi ACIP BAKABILIR. "Calisiyor" demekle gostermek ayni sey
    # degil: `run_command` ile sunucuyu baslatir, `preview_open` ile acar,
    # tiklar ve `browser_screenshot` ile kanit birakir.
    "qa": [
        "search_knowledge", "read_project_state", "update_task",
        "read_file", "write_file", "edit_file", "list_dir", "glob_files", "grep_files",
        "run_command", "record_gaps", "save_artifact",
        "start_service", "service_log", "stop_service", "list_services",
        "preview_open", "browser_snapshot", "browser_click", "browser_type",
        "browser_back", "browser_console", "browser_screenshot",
    ],
    "reviewer": [
        "search_knowledge", "read_project_state",
        "read_file", "list_dir", "glob_files", "grep_files",
        "run_command", "record_gaps", "update_task", "save_artifact",
    ],
    "staging": [
        "search_knowledge", "read_project_state", "update_task",
        "read_file", "write_file", "edit_file", "list_dir", "glob_files", "grep_files",
        "run_command", "record_gaps", "save_artifact",
        "start_service", "service_log", "stop_service",
        "preview_open", "browser_snapshot", "browser_console", "browser_screenshot",
    ],
    # Canli ajan dosya YAZMAZ: incelenmis ve staging'de dogrulanmis olani dagitir.
    "live": [
        "search_knowledge", "read_project_state", "update_task",
        "read_file", "list_dir", "glob_files", "grep_files",
        "run_command", "record_gaps", "save_artifact",
    ],
}

# Gorev seridi -> uygulayan ajan. Plan fazi her goreve bir serit atar.
LANE_ROLE: dict[str, str] = {
    "backend": "backend",
    "frontend": "frontend",
    "qa": "qa",
    "test": "qa",
    "infra": "backend",
    "docs": "backend",
}

__all__ = [
    "ALL_TOOLS",
    "BROWSER_TOOLS",
    "FILESYSTEM_TOOLS",
    "KNOWLEDGE_TOOLS",
    "PROJECT_TOOLS",
    "SERVICE_TOOLS",
    "SHELL_TOOLS",
    "LANE_ROLE",
    "TOOLSETS",
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "IMAGE_TOOLS",
    "WEB_TOOLS",
    "build_registry",
    "json_block",
]
