#!/bin/bash
set -uxo pipefail
cd /testbed
git config --global --add safe.directory /testbed
cd /testbed
git checkout 920a65634cd89d47f2a561e17b99c2a5233cfa39 packages/jupyter-ai-magics/jupyter_ai_magics/tests/test_magics.py
git apply --verbose --reject - <<'EOF_114329324912'
diff --git a/packages/jupyter-ai-magics/jupyter_ai_magics/tests/test_magics.py b/packages/jupyter-ai-magics/jupyter_ai_magics/tests/test_magics.py
index ec1635347..67189afa9 100644
--- a/packages/jupyter-ai-magics/jupyter_ai_magics/tests/test_magics.py
+++ b/packages/jupyter-ai-magics/jupyter_ai_magics/tests/test_magics.py
@@ -1,11 +1,50 @@
+from unittest.mock import patch
+
 from IPython import InteractiveShell
+from jupyter_ai_magics.magics import AiMagics
+from pytest import fixture
 from traitlets.config.loader import Config
 
 
-def test_aliases_config():
+@fixture
+def ip() -> InteractiveShell:
     ip = InteractiveShell()
     ip.config = Config()
+    return ip
+
+
+def test_aliases_config(ip):
     ip.config.AiMagics.aliases = {"my_custom_alias": "my_provider:my_model"}
     ip.extension_manager.load_extension("jupyter_ai_magics")
     providers_list = ip.run_line_magic("ai", "list").text
     assert "my_custom_alias" in providers_list
+
+
+def test_default_model_cell(ip):
+    ip.config.AiMagics.default_language_model = "my-favourite-llm"
+    ip.extension_manager.load_extension("jupyter_ai_magics")
+    with patch.object(AiMagics, "run_ai_cell", return_value=None) as mock_run:
+        ip.run_cell_magic("ai", "", cell="Write code for me please")
+        assert mock_run.called
+        cell_args = mock_run.call_args.args[0]
+        assert cell_args.model_id == "my-favourite-llm"
+
+
+def test_non_default_model_cell(ip):
+    ip.config.AiMagics.default_language_model = "my-favourite-llm"
+    ip.extension_manager.load_extension("jupyter_ai_magics")
+    with patch.object(AiMagics, "run_ai_cell", return_value=None) as mock_run:
+        ip.run_cell_magic("ai", "some-different-llm", cell="Write code for me please")
+        assert mock_run.called
+        cell_args = mock_run.call_args.args[0]
+        assert cell_args.model_id == "some-different-llm"
+
+
+def test_default_model_error_line(ip):
+    ip.config.AiMagics.default_language_model = "my-favourite-llm"
+    ip.extension_manager.load_extension("jupyter_ai_magics")
+    with patch.object(AiMagics, "handle_error", return_value=None) as mock_run:
+        ip.run_cell_magic("ai", "error", cell=None)
+        assert mock_run.called
+        cell_args = mock_run.call_args.args[0]
+        assert cell_args.model_id == "my-favourite-llm"

EOF_114329324912
: '>>>>> Start Test Output'
pytest -vv -rA
: '>>>>> End Test Output'
git checkout 920a65634cd89d47f2a561e17b99c2a5233cfa39 packages/jupyter-ai-magics/jupyter_ai_magics/tests/test_magics.py
