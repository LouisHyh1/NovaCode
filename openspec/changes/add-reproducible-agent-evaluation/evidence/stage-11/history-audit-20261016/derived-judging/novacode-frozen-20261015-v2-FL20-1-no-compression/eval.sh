#!/bin/bash
set -uxo pipefail
cd /testbed
git config --global --add safe.directory /testbed
cd /testbed
git checkout a55db38c061522a2b6397516df1eeedf2e8b4dc5 packages/jupyter-ai/jupyter_ai/tests/test_handlers.py
git apply --verbose --reject - <<'EOF_114329324912'
diff --git a/packages/jupyter-ai/jupyter_ai/tests/test_handlers.py b/packages/jupyter-ai/jupyter_ai/tests/test_handlers.py
index d0ccbe552..c1ca7b098 100644
--- a/packages/jupyter-ai/jupyter_ai/tests/test_handlers.py
+++ b/packages/jupyter-ai/jupyter_ai/tests/test_handlers.py
@@ -9,6 +9,7 @@
 from jupyter_ai.config_manager import ConfigManager
 from jupyter_ai.extension import DEFAULT_HELP_MESSAGE_TEMPLATE
 from jupyter_ai.handlers import RootChatHandler
+from jupyter_ai.history import BoundedChatHistory
 from jupyter_ai.models import (
     ChatClient,
     ClosePendingMessage,
@@ -69,6 +70,7 @@ def broadcast_message(message: Message) -> None:
             root_chat_handlers={"root": root_handler},
             model_parameters={},
             chat_history=[],
+            llm_chat_memory=BoundedChatHistory(k=2),
             root_dir="",
             preferred_dir="",
             dask_client_future=None,

EOF_114329324912
: '>>>>> Start Test Output'
pytest -vv -rA
: '>>>>> End Test Output'
git checkout a55db38c061522a2b6397516df1eeedf2e8b4dc5 packages/jupyter-ai/jupyter_ai/tests/test_handlers.py
