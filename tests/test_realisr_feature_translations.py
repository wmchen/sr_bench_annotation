import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from PyQt6 import QtCore, QtWidgets

    import anylabeling.resources.resources  # noqa: F401

    QT_AVAILABLE = True
except Exception:
    QT_AVAILABLE = False


class RealISRFeatureTranslationsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        catalog_path = (
            Path(__file__).resolve().parents[1]
            / "anylabeling/resources/translations/zh_CN.ts"
        )
        cls.root = ET.parse(catalog_path).getroot()

    def translations_for(self, context_name):
        context = next(
            context
            for context in self.root.findall("context")
            if context.findtext("name") == context_name
        )
        return {
            message.findtext("source"): message.findtext("translation")
            for message in context.findall("message")
        }

    def test_auto_labeling_strings_are_localized(self):
        translations = self.translations_for("AutoLabelingWidget")
        expected = {
            "Inference Mode": "推理模式",
            "Full Image": "全图推理",
            "Instance": "实例推理",
            "Real-ISR mode is not active.": "Real-ISR 模式未启用。",
            "Inference is already running.": "推理正在执行。",
            "Wait for the model to finish loading.": "请等待模型加载完成。",
            "Select a model before auto labeling.": "自动标注前请先选择模型。",
            "The selected model is incompatible with this Real-ISR task.": (
                "所选模型与当前 Real-ISR 任务不兼容。"
            ),
            "Auto labeling is only available in HR.": (
                "自动标注仅可在 HR 视图中执行。"
            ),
            "No HR image is available.": "当前没有可用的 HR 图像。",
            (
                "Full-image inference is disabled because HR already has "
                "annotations."
            ): "HR 已存在标注，已禁用全图推理。",
            "Select exactly one annotation box for instance inference.": (
                "实例推理需要恰好选中一个标注框。"
            ),
            "The selected annotation type cannot be recognized.": (
                "无法识别所选标注的类型。"
            ),
            "text detection and recognition": "文本检测和识别",
            "face detection": "人脸检测",
            "Confirm Full-Image Inference": "确认全图推理",
            "Run %s on the entire HR image?": "是否对整张 HR 图像执行%s？",
            "Could not save the current HR annotation.": (
                "无法保存当前 HR 标注。"
            ),
            "The selected annotation has no region ID.": (
                "所选标注缺少区域 ID。"
            ),
        }
        for source, translation in expected.items():
            self.assertEqual(translations.get(source), translation)

    def test_focus_selected_object_is_localized(self):
        labeling = self.translations_for("LabelingWidget")
        settings = self.translations_for("SettingsDialog")
        self.assertEqual(labeling.get("Focus Selected Object"), "聚焦选中对象")
        self.assertEqual(settings.get("Focus Selected Object"), "聚焦选中对象")

    def test_multiview_button_is_localized_in_english_and_chinese(self):
        chinese = self.translations_for("LabelingWidget")
        english_catalog = (
            Path(__file__).resolve().parents[1]
            / "anylabeling/resources/translations/en_US.ts"
        )
        english_root = ET.parse(english_catalog).getroot()
        english_context = next(
            context
            for context in english_root.findall("context")
            if context.findtext("name") == "LabelingWidget"
        )
        english = {
            message.findtext("source"): message.findtext("translation")
            for message in english_context.findall("message")
        }

        self.assertEqual(chinese.get("Tile Multiple Views"), "多视图平铺")
        self.assertEqual(
            english.get("Tile Multiple Views"), "Tile Multiple Views"
        )

    def test_redundant_draft_warning_is_localized(self):
        translations = self.translations_for("LabelingWidget")
        self.assertEqual(
            translations.get("Redundant Real-ISR drafts"),
            "冗余的 Real-ISR 草稿",
        )
        self.assertEqual(
            translations.get(
                "These drafts are identical to their formal annotations:\n"
                "%s\n\n"
                "Open each corresponding image and save it again to clear "
                "the redundant draft."
            ),
            "以下草稿与正式标注完全相同：\n%s\n\n"
            "请打开对应图像并重新保存，以清除冗余草稿。",
        )


class RealISRFocusToolbarStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source_path = (
            Path(__file__).resolve().parents[1]
            / "anylabeling/views/labeling/label_widget.py"
        )
        cls.tree = ast.parse(source_path.read_text(encoding="utf-8"))

    def test_focus_action_reuses_fit_width_icon(self):
        assignment = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "focus_selected_object"
                for target in node.targets
            )
        )
        self.assertIsInstance(assignment.value, ast.Call)
        self.assertEqual(assignment.value.args[3].value, "fit-width")

    def test_focus_button_is_immediately_before_zoom_level(self):
        assignment = next(
            node
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Attribute) and target.attr == "tool"
                for target in node.targets
            )
        )
        action_names = [
            element.id
            for element in assignment.value.elts
            if isinstance(element, ast.Name)
        ]
        self.assertEqual(
            action_names[-3:],
            ["fit_width", "focus_selected_object", "zoom"],
        )

    def test_view_controls_are_immediately_before_commit_button(self):
        layout_operations = []
        for node in ast.walk(self.tree):
            if (
                not isinstance(node, ast.Call)
                or not node.args
                or not isinstance(node.func, ast.Attribute)
            ):
                continue
            function = node.func
            if (
                function.attr not in {"addWidget", "addLayout"}
                or not isinstance(function.value, ast.Name)
                or function.value.id != "realisr_panel_layout"
            ):
                continue
            argument = node.args[0]
            name = None
            if isinstance(argument, ast.Attribute):
                name = argument.attr
            elif isinstance(argument, ast.Name):
                name = argument.id
            if name is not None:
                layout_operations.append((node.lineno, function.attr, name))

        layout_operations.sort()
        controls = [
            (operation, name) for _, operation, name in layout_operations
        ]

        self.assertIn(
            [
                ("addWidget", "realisr_dashboard"),
                ("addWidget", "realisr_multiview_button"),
                ("addLayout", "realisr_variant_layout"),
                ("addWidget", "realisr_commit_button"),
            ],
            [controls[index : index + 4] for index in range(len(controls))],
        )


@unittest.skipUnless(QT_AVAILABLE, "PyQt6 translation runtime is unavailable")
class RealISRCompiledTranslationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtCore.QCoreApplication.instance()
        if cls.app is None:
            cls.app = QtWidgets.QApplication([])
        cls.translator = QtCore.QTranslator()
        if not cls.translator.load(":/languages/translations/zh_CN.qm"):
            raise AssertionError("Could not load compiled zh_CN translation")
        cls.app.installTranslator(cls.translator)

    @classmethod
    def tearDownClass(cls):
        cls.app.removeTranslator(cls.translator)

    def test_compiled_feature_translations(self):
        checks = {
            ("AutoLabelingWidget", "Inference Mode"): "推理模式",
            ("AutoLabelingWidget", "Full Image"): "全图推理",
            ("AutoLabelingWidget", "Instance"): "实例推理",
            (
                "AutoLabelingWidget",
                "Confirm Full-Image Inference",
            ): "确认全图推理",
            ("LabelingWidget", "Focus Selected Object"): "聚焦选中对象",
            ("LabelingWidget", "Tile Multiple Views"): "多视图平铺",
            (
                "LabelingWidget",
                "Redundant Real-ISR drafts",
            ): "冗余的 Real-ISR 草稿",
            (
                "LabelingWidget",
                "These drafts are identical to their formal annotations:\n"
                "%s\n\n"
                "Open each corresponding image and save it again to clear "
                "the redundant draft.",
            ): "以下草稿与正式标注完全相同：\n%s\n\n"
            "请打开对应图像并重新保存，以清除冗余草稿。",
            ("SettingsDialog", "Focus Selected Object"): "聚焦选中对象",
        }
        for (context, source), expected in checks.items():
            self.assertEqual(
                QtCore.QCoreApplication.translate(context, source),
                expected,
            )

        english_translator = QtCore.QTranslator()
        self.assertTrue(
            english_translator.load(":/languages/translations/en_US.qm")
        )
        self.app.installTranslator(english_translator)
        try:
            self.assertEqual(
                QtCore.QCoreApplication.translate(
                    "LabelingWidget", "Tile Multiple Views"
                ),
                "Tile Multiple Views",
            )
        finally:
            self.app.removeTranslator(english_translator)


if __name__ == "__main__":
    unittest.main()
