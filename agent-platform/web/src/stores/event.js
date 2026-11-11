/**
 * 生成流水线的过程态：阶段条 / 产物文件 / 编译日志。
 *
 * 生命周期是「一次生成」：开始即清空，结束保留到用户切会话或新对话——
 * 生成完立刻抹掉，用户就看不到这次到底改了什么文件了。
 */
import { defineStore } from "pinia";

export const useEventStore = defineStore("event", {
  state: () => ({
    mode: "chat",      // chat | generation
    running: false,
    steps: [],         // {step, ok, detail}
    files: [],
    logs: [],
    failed: false,
    filesOpen: false,
    logsOpen: false,
  }),

  getters: {
    stepCount: (state) => state.steps.length,
    fileCount: (state) => state.files.length,
    visibleFiles(state) {
      return state.filesOpen ? state.files : state.files.slice(0, 6);
    },
    visibleLogs(state) {
      return state.logsOpen ? state.logs : state.logs.slice(-6);
    },
  },

  actions: {
    reset() {
      this.mode = "chat";
      this.running = false;
      this.steps = [];
      this.files = [];
      this.logs = [];
      this.failed = false;
      this.filesOpen = false;
      this.logsOpen = false;
    },

    start(mode = "chat") {
      this.reset();
      this.mode = mode;
      this.running = true;
    },

    addStep(event) {
      this.steps.push({ step: event.step || "", ok: !!event.ok, detail: event.detail || "" });
      if (!event.ok) this.failed = true;
    },

    addFile(path) {
      if (!path || this.files.includes(path)) return;
      this.files.push(path);
    },

    addLog(text) {
      if (!text) return;
      this.logs.push(text);
      // 日志只留最近 80 行：够定位问题，又不会把内存撑爆
      if (this.logs.length > 80) this.logs.splice(0, this.logs.length - 80);
    },

    fail() {
      this.failed = true;
      this.running = false;
      this.logsOpen = true;   // 失败时自动展开日志末尾，省一次点击
    },

    finish() {
      this.running = false;
    },
  },
});
