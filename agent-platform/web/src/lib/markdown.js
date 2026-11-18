/**
 * Markdown 渲染（《前端交互设计》4.1 的三步链条）：
 *   markdown-it（html:false） → DOMPurify 白名单 → v-html
 *
 * 一步都不能省：需求文档、业务数据都是不可信输入，模型会原样复述进对话，
 * 放开 HTML 等于把注入面从「模型」扩大到「任何上传的文档」。
 */
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";

const md = new MarkdownIt({
  html: false,     // 原始 HTML 当文本处理
  linkify: true,   // 裸链接自动成链
  breaks: true,    // 单换行即换行（对话里更符合预期）
  typographer: false,
});

const baseLinkOpen = md.renderer.rules.link_open
  || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));

// 外链统一新窗口 + rel，防止 window.opener 被拿走
md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer");
  return baseLinkOpen(tokens, idx, options, env, self);
};

export function renderMarkdown(text) {
  const html = md.render(String(text == null ? "" : text));
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    ADD_ATTR: ["target", "rel"],
  });
}
