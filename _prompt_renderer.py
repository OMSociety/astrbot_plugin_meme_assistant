"""Prompt 渲染器 — 基于 Jinja2 模板引擎，支持独立测试和多语言

用法:
    from ._prompt_renderer import PromptRenderer
    renderer = PromptRenderer(template_dir="templates/prompts")
    result = renderer.render("system_prompt", categories="...", max_emotions=2)
"""

from pathlib import Path

import jinja2


class PromptRenderer:
    """Jinja2 Prompt 渲染器。

    支持:
    - 模板独立测试：直接传入变量渲染，不依赖插件运行时
    - 多语言：通过 locale 参数切换到对应语言目录
    - 缓存：Template 对象在首次加载后缓存，避免重复 IO
    """

    def __init__(self, template_dir: str | Path, locale: str | None = None):
        """初始化渲染器。

        Args:
            template_dir: 模板根目录路径
            locale: 语言代码，如 'zh_CN'。为 None 时使用根目录模板。
        """
        self._root_dir = Path(template_dir)
        self._locale = locale
        self._cache: dict[str, jinja2.Template] = {}
        self._env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(self._root_dir)),
            autoescape=False,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # 语言目录
        self._locale_dir = self._root_dir / "locales" / locale if locale else None

    def set_locale(self, locale: str | None):
        """切换语言。"""
        self._locale = locale
        self._locale_dir = self._root_dir / "locales" / locale if locale else None
        self._cache.clear()

    def render(self, template_name: str, **kwargs) -> str:
        """渲染指定模板。

        查找顺序: locales/{lang}/{name} > {name}

        Args:
            template_name: 模板文件名（不含路径）
            **kwargs: 模板变量

        Returns:
            渲染后的字符串
        """
        # 尝试本地化模板
        if self._locale_dir:
            locale_path = self._locale_dir / template_name
            if locale_path.exists():
                key = f"{self._locale}/{template_name}"
                if key not in self._cache:
                    self._env.loader = jinja2.FileSystemLoader(str(self._locale_dir))
                    self._cache[key] = self._env.get_template(template_name)
                return self._cache[key].render(**kwargs)

        # 回退到根目录模板
        if template_name not in self._cache:
            self._env.loader = jinja2.FileSystemLoader(str(self._root_dir))
            self._cache[template_name] = self._env.get_template(template_name)
        return self._cache[template_name].render(**kwargs)

    def render_raw(self, template_string: str, **kwargs) -> str:
        """从字符串渲染（用于测试和用户自定义 prompt 片段）。"""
        tpl = jinja2.Template(template_string)
        return tpl.render(**kwargs)

    # ── 便捷方法 ──────────────────────────────

    def render_system_prompt(
        self,
        categories: str,
        max_emotions: int,
        prompt_head: str | None = None,
        prompt_tail_1: str | None = None,
        prompt_tail_2: str | None = None,
    ) -> str:
        """渲染角色 system prompt 追加内容。"""
        return self.render(
            "system_prompt.j2",
            categories=categories,
            max_emotions=max_emotions,
            prompt_head=prompt_head,
            prompt_tail_1=prompt_tail_1,
            prompt_tail_2=prompt_tail_2,
        )

    def render_meme_tool_prompt(
        self, categories: list[str], demo_count: int = 20
    ) -> str:
        """渲染 send_meme 工具注入提示。"""
        return self.render(
            "meme_tool.j2", categories=categories, demo_count=demo_count
        )

    def render_vision_identify_prompt(self) -> str:
        """渲染表情包识别 Vision prompt。"""
        return self.render("vision_identify.j2")

    def render_emotion_llm_prompt(
        self, valid_labels: list[str], text: str
    ) -> str:
        """渲染 LLM 情感识别 prompt。"""
        return self.render("emotion_llm.j2", valid_labels=valid_labels, text=text)
