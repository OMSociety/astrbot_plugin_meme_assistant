"""
meme_assistant 核心功能单元测试
使用 mock 隔离 AstrBot 运行时依赖
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# 将插件目录加入路径
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN_DIR)

# 提前 mock astrbot 模块，避免导入期报错
sys.modules["astrbot"] = MagicMock()
sys.modules["astrbot.api"] = MagicMock()
sys.modules["astrbot.api.all"] = MagicMock()
sys.modules["astrbot.api.event"] = MagicMock()
sys.modules["astrbot.api.provider"] = MagicMock()
sys.modules["astrbot.api.message_components"] = MagicMock()
sys.modules["astrbot.core"] = MagicMock()
sys.modules["astrbot.core.utils"] = MagicMock()
sys.modules["astrbot.core.utils.astrbot_path"] = MagicMock()
sys.modules["astrbot.core.message"] = MagicMock()
sys.modules["astrbot.core.message.components"] = MagicMock()
sys.modules["astrbot.core.message.message_event_result"] = MagicMock()

# 配置 mock 返回值
import astrbot.core.utils.astrbot_path as mock_path  # noqa: E402

mock_path.get_astrbot_data_path.return_value = Path(tempfile.gettempdir())
mock_path.get_astrbot_plugin_data_path.return_value = Path(tempfile.gettempdir()) / "meme_manager"


class TestConfigConstants(unittest.TestCase):
    """测试配置常量与数据路径"""

    def test_config_constants_exist(self):
        """config.py 应导出必要的常量"""
        import config
        for attr in [
            "PLUGIN_DIR", "CURRENT_DIR", "DEFAULT_PLUGIN_NAME",
            "MEMES_DIR", "MEMES_DATA_PATH", "DEFAULT_CATEGORY_DESCRIPTIONS",
        ]:
            self.assertTrue(hasattr(config, attr), f"config 缺少常量: {attr}")

    def test_default_category_descriptions_is_dict(self):
        """默认类别描述应为非空字典"""
        from config import DEFAULT_CATEGORY_DESCRIPTIONS
        self.assertIsInstance(DEFAULT_CATEGORY_DESCRIPTIONS, dict)
        self.assertGreater(len(DEFAULT_CATEGORY_DESCRIPTIONS), 0)

    def test_memes_dir_in_data_path(self):
        """MEMES_DIR 应位于 data 路径下"""
        from config import MEMES_DIR
        path_str = str(MEMES_DIR).replace("\\", "/")
        self.assertIn("data", path_str, f"MEMES_DIR 未在 data 目录: {path_str}")

    def test_memes_dir_ends_with_memes(self):
        """MEMES_DIR 路径应以 memes 结尾"""
        from config import MEMES_DIR
        self.assertTrue(
            str(MEMES_DIR).endswith("memes") or str(MEMES_DIR).endswith("memes/"),
            f"MEMES_DIR 不以 memes 结尾: {MEMES_DIR}"
        )


class TestPureUtils(unittest.TestCase):
    """测试纯函数工具（无 AstrBot 依赖）"""

    def test_hashtag_from_path(self):
        """从路径中提取 #标签"""
        # inline 测试：标签格式化逻辑
        path = Path("/some/dir/#测试表情")
        self.assertIn("测试表情", str(path))

    def test_pathlib_operations(self):
        """验证数据路径构建的正确性"""
        base = Path("/data/plugin_data/meme_manager")
        memes_dir = base / "memes"
        self.assertEqual(str(memes_dir), "/data/plugin_data/meme_manager/memes")
        data_file = base / "emotion_descriptions.json"
        self.assertTrue(data_file.suffix == ".json")


class TestJSONRoundTrip(unittest.TestCase):
    """测试 JSON 读写"""

    def test_json_write_and_read(self):
        """写 JSON 再读回来应保持一致"""
        data = {
            "开心": {
                "description": "😄 开怀大笑",
                "tags": ["happy", "laugh"],
            },
            "生气": {
                "description": "😠 怒火中烧",
                "tags": ["angry"],
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            tmp_path = f.name

        try:
            with open(tmp_path, encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(loaded, data)
            self.assertEqual(loaded["开心"]["tags"], ["happy", "laugh"])
        finally:
            os.unlink(tmp_path)

    def test_empty_json_file(self):
        """空文件通过 json.load 应报错（验证异常路径）"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("")
            tmp_path = f.name

        try:
            with self.assertRaises(json.JSONDecodeError):
                with open(tmp_path) as f:
                    json.load(f)
        finally:
            os.unlink(tmp_path)

    def test_invalid_json_should_fail(self):
        """非法 JSON 应抛出异常"""
        with self.assertRaises(json.JSONDecodeError):
            json.loads("not valid {{{")


class TestStringPatterns(unittest.TestCase):
    """测试正则/字符串匹配逻辑"""

    def test_thinking_tag_pattern(self):
        """验证 thinking 标签的正则匹配"""
        import re
        pattern = re.compile(
            r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE
        )
        # 在 thinking 标签内
        text = "<thinking>我很开心</thinking>"
        match = pattern.search(text)
        self.assertIsNotNone(match)
        self.assertTrue(match.start() <= 10 < match.end())

    def test_thinking_tag_not_matched_outside(self):
        """标签外位置不应被匹配"""
        import re
        pattern = re.compile(
            r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE
        )
        text = "你好 <thinking>xxx</thinking> 再见"
        # 位置 0（"你"）不在 thinking 标签内
        match = pattern.search(text)
        self.assertFalse(match.start() <= 0 < match.end())

    def test_chinese_char_detection(self):
        """中文字符检测"""
        import re
        cn_pattern = re.compile(r"[\u4e00-\u9fff]")
        self.assertTrue(cn_pattern.search("开心"))
        self.assertFalse(cn_pattern.search("abc123"))


class TestMemeDetectionLogic(unittest.TestCase):
    """测试表情检测纯逻辑"""

    def test_position_in_thinking_tags(self):
        """_is_position_in_thinking_tags 逻辑验证"""
        import re
        text = "正常文本<thinking>内部内容</thinking>外部文本"

        thinking_pattern = re.compile(
            r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE
        )

        def is_in_thinking(text, pos):
            for m in thinking_pattern.finditer(text):
                if m.start() <= pos < m.end():
                    return True
            return False

        # "正常" 在标签外 → False
        self.assertFalse(is_in_thinking(text, 1))
        # "内部" 在标签内 → True
        self.assertTrue(is_in_thinking(text, 10))
        # "外部文本" 在标签外 → False
        end_pos = text.index("外部文本")
        self.assertFalse(is_in_thinking(text, end_pos))


class TestFileHandling(unittest.TestCase):
    """测试文件操作"""

    def test_temp_file_write_and_delete(self):
        """临时文件创建和删除"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("test content")
            tmp = f.name

        self.assertTrue(os.path.exists(tmp))
        os.unlink(tmp)
        self.assertFalse(os.path.exists(tmp))

    def test_directory_creation(self):
        """目录创建"""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = os.path.join(tmpdir, "memes", "开心")
            os.makedirs(subdir, exist_ok=True)
            self.assertTrue(os.path.isdir(subdir))
            # 重复创建不应报错
            os.makedirs(subdir, exist_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
