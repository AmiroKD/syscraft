/**
 * 身份：令牌 + 当前用户。
 *
 * 令牌只放 sessionStorage（关掉标签页即失效），不放 localStorage：
 * 演示机常常是公共电脑，少留一份是一份。真正的凭据校验始终在服务端。
 */
import { defineStore } from "pinia";
import { api, setToken } from "../lib/http";

const TOKEN_KEY = "token";
const LLM_ROLES = new Set(["SUPER_ADMIN", "ADMIN"]);
// 管理员面板的入口角色。平台侧还有一道同样的门（app.py ADMIN_PANEL_ROLES），
// 真正的能力校验（MANAGE_USER、层级）在产物侧 OperatorContext——前端只管显隐。
const ADMIN_ROLES = new Set(["SUPER_ADMIN", "ADMIN"]);

export const useAuthStore = defineStore("auth", {
  state: () => ({
    token: sessionStorage.getItem(TOKEN_KEY) || "",
    me: null,
    booting: true,
  }),

  getters: {
    loggedIn: (state) => !!state.me,
    name: (state) => state.me?.username || "用户",
    roleLabel: (state) => state.me?.roleName || state.me?.roleCode || "成员",
    initial: (state) => (state.me?.username || "U").slice(0, 2).toUpperCase(),
    canEditLlm: (state) => !!state.me && LLM_ROLES.has(state.me.roleCode),
    isAdmin: (state) => !!state.me && ADMIN_ROLES.has(state.me.roleCode),
  },

  actions: {
    applyToken(token) {
      this.token = token || "";
      setToken(this.token);
      if (this.token) sessionStorage.setItem(TOKEN_KEY, this.token);
      else sessionStorage.removeItem(TOKEN_KEY);
    },

    async login(username, password) {
      const data = await api("POST", "/api/auth/login", { username, password });
      this.applyToken(data.token);
      this.me = data.user;
      return data.user;
    },

    /** 启动时用已存令牌换身份；失败即视为未登录，不弹错 */
    async restore() {
      if (!this.token) {
        this.booting = false;
        return false;
      }
      setToken(this.token);
      try {
        this.me = await api("GET", "/api/auth/me");
        return true;
      } catch (error) {
        this.applyToken("");
        this.me = null;
        return false;
      } finally {
        this.booting = false;
      }
    },

    logout() {
      this.applyToken("");
      this.me = null;
    },
  },
});
