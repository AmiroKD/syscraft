/**
 * 纯 UI 状态：弹层、侧栏、轻提示、确认框。
 *
 * 单独一个 store 是为了让业务 store 不掺 DOM 交互——chat 只管消息与流，
 * 「弹哪个框」不该出现在它的代码里。
 */
import { defineStore } from "pinia";

let toastSeed = 0;

export const useUiStore = defineStore("ui", {
  state: () => ({
    toasts: [],
    dialog: null,        // 'settings' | 'profile' | 'admin' | 'help' | null
    sidebarOpen: false,
    confirm: { open: false, title: "", text: "", okText: "确定", resolve: null },
  }),

  actions: {
    toast(message, tone = "info") {
      const id = ++toastSeed;
      this.toasts.push({ id, message, tone });
      setTimeout(() => this.dismiss(id), 3200);
    },

    dismiss(id) {
      this.toasts = this.toasts.filter((item) => item.id !== id);
    },

    openDialog(name) { this.dialog = name; },
    closeDialog() { this.dialog = null; },

    openSidebar() { this.sidebarOpen = true; },
    closeSidebar() { this.sidebarOpen = false; },

    /** 原生确认框的 Promise 版本：调用点写 `if (!await ui.ask(...)) return` */
    ask(title, text, okText = "确定") {
      return new Promise((resolve) => {
        this.confirm = { open: true, title, text, okText, resolve };
      });
    },

    answerConfirm(value) {
      const resolve = this.confirm.resolve;
      this.confirm = { open: false, title: "", text: "", okText: "确定", resolve: null };
      resolve?.(value);
    },
  },
});
