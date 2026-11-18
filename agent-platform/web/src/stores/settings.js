/**
 * 界面设置与模型配置。
 *
 * 两条边界：
 * - 界面设置（主题/字号/工具轨迹/发送键）只存本机 localStorage，不上服务端；
 * - 模型配置另存一份到 sessionStorage 作为「本会话上次填的」（Key 敏感度与
 *   token 同级，不落长期存储）；服务端只回「平台是否已配置」的布尔值，不回传 Key。
 */
import { defineStore } from "pinia";
import { api } from "../lib/http";

const SETTINGS_KEY = "syscraft.settings";
const LLM_KEY = "syscraft.llm";

const DEFAULTS = {
  theme: "system",   // light | dark | system
  fontSize: "md",    // sm | md | lg
  showTools: true,   // 是否展示工具调用轨迹
  sendKey: "enter",  // enter | mod
};

function loadSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    const merged = { ...DEFAULTS, ...saved };
    if (!["light", "dark", "system"].includes(merged.theme)) merged.theme = "system";
    if (!["sm", "md", "lg"].includes(merged.fontSize)) merged.fontSize = "md";
    if (!["enter", "mod"].includes(merged.sendKey)) merged.sendKey = "enter";
    merged.showTools = merged.showTools !== false;
    return merged;
  } catch (e) {
    return { ...DEFAULTS };
  }
}

function loadLlm() {
  try {
    // Key 与 token 同生命周期：存 sessionStorage，随标签页关闭即清，不落长期存储
    const raw = JSON.parse(sessionStorage.getItem(LLM_KEY) || "{}");
    return {
      baseUrl: typeof raw.baseUrl === "string" ? raw.baseUrl : "",
      apiKey: typeof raw.apiKey === "string" ? raw.apiKey : "",
      model: typeof raw.model === "string" ? raw.model : "",
    };
  } catch (e) {
    return { baseUrl: "", apiKey: "", model: "" };
  }
}

export const useSettingsStore = defineStore("settings", {
  state: () => ({
    ...loadSettings(),
    llm: loadLlm(),
    serverConfigured: false,
    llmBusy: false,
    llmStatus: { text: "", tone: "" },
    themeTick: 0,   // 主题切换计数：图表用它触发重绘
  }),

  getters: {
    isDark(state) {
      return state.theme === "dark"
        || (state.theme === "system"
          && window.matchMedia("(prefers-color-scheme: dark)").matches);
    },
    modelLabel(state) {
      if (state.llm.model) return state.llm.model;
      return state.serverConfigured ? "平台默认" : "配置模型";
    },
  },

  actions: {
    persist() {
      try {
        const { theme, fontSize, showTools, sendKey } = this;
        localStorage.setItem(SETTINGS_KEY, JSON.stringify({ theme, fontSize, showTools, sendKey }));
      } catch (e) { /* 隐私模式忽略 */ }
    },

    applyTheme() {
      document.documentElement.dataset.theme = this.isDark ? "dark" : "light";
      document.documentElement.dataset.font = this.fontSize;
      this.themeTick += 1;
    },

    set(key, value) {
      this[key] = value;
      this.persist();
      this.applyTheme();
    },

    reset() {
      Object.assign(this, DEFAULTS);
      this.persist();
      this.applyTheme();
    },

    /** 跟随系统时，系统切换要跟着变 */
    watchSystemTheme() {
      const media = window.matchMedia("(prefers-color-scheme: dark)");
      media.addEventListener("change", () => {
        if (this.theme === "system") this.applyTheme();
      });
    },

    async loadServerStatus() {
      try {
        const data = await api("GET", "/api/settings/llm");
        this.serverConfigured = !!data.configured;
      } catch (error) {
        if (error.status !== 401) this.serverConfigured = false;
      }
    },

    async saveLlm() {
      if (this.llmBusy) return;
      const payload = {
        baseUrl: this.llm.baseUrl.trim(),
        apiKey: this.llm.apiKey.trim(),
        model: this.llm.model.trim(),
      };
      if (!payload.baseUrl || !payload.apiKey || !payload.model) {
        this.llmStatus = { text: "请完整填写 Base URL、API Key 和模型名", tone: "fail" };
        return false;
      }
      this.llmBusy = true;
      this.llmStatus = { text: "保存中…", tone: "" };
      try {
        await api("PUT", "/api/settings/llm", payload);
        try {
          sessionStorage.setItem(LLM_KEY, JSON.stringify(payload));
        } catch (e) { /* 忽略 */ }
        this.serverConfigured = true;
        this.llmStatus = { text: "已保存并应用到平台，本浏览器本次会话内有效", tone: "ok" };
        return true;
      } catch (error) {
        this.llmStatus = { text: "保存失败：" + error.message, tone: "fail" };
        return false;
      } finally {
        this.llmBusy = false;
      }
    },

    async testLlm() {
      if (this.llmBusy) return;
      const payload = {
        baseUrl: this.llm.baseUrl.trim(),
        apiKey: this.llm.apiKey.trim(),
        model: this.llm.model.trim(),
      };
      if (!payload.baseUrl || !payload.apiKey || !payload.model) {
        this.llmStatus = { text: "请完整填写 Base URL、API Key 和模型名", tone: "fail" };
        return;
      }
      this.llmBusy = true;
      this.llmStatus = { text: "连接中…最长等待 45 秒", tone: "" };
      try {
        const result = await api("POST", "/api/settings/llm/test", payload, { timeout: 45000 });
        this.llmStatus = {
          text: result.message || (result.ok ? "连接成功" : "连接失败"),
          tone: result.ok ? "ok" : "fail",
        };
      } catch (error) {
        this.llmStatus = { text: "测试失败：" + error.message, tone: "fail" };
      } finally {
        this.llmBusy = false;
      }
    },

    clearLlmCache() {
      try {
        sessionStorage.removeItem(LLM_KEY);
      } catch (e) { /* 忽略 */ }
      this.llm = { baseUrl: "", apiKey: "", model: "" };
      this.llmStatus = {
        text: this.serverConfigured
          ? "已清除本机缓存；平台仍在使用已有配置"
          : "已清除本机缓存",
        tone: "",
      };
    },
  },
});
