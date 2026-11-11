/**
 * 对话主状态：会话列表、消息、流式过程。
 *
 * 三条不变量（都是从旧实现踩出来的坑，重构后必须保持）：
 * 1. 新会话首轮必须采纳服务端回的 sessionId，否则每条消息会裂成新会话；
 * 2. 流事件只渲染进「流所属会话」的视图，用户切走再切回不能串台；
 * 3. 同一轮多次查询只展示最后一张表，中间试错不堆历史。
 */
import { defineStore } from "pinia";
import { api, HttpError } from "../lib/http";
import { openStream } from "../lib/sse";
import { useAuthStore } from "./auth";
import { useEventStore } from "./event";
import { useUiStore } from "./ui";

const SESSION_KEY = "sessionId";

let seed = 0;
const nextId = () => `m${++seed}`;

function normalizeContent(type, content) {
  if (type === "table") return content && typeof content === "object" ? content : null;
  if (type === "text") {
    if (typeof content === "string") return content;
    return content?.text || content?.message || "";
  }
  if (type === "notice") {
    return typeof content === "string" ? content : content?.text || content?.message || "";
  }
  if (type === "error") {
    return typeof content === "string" ? content : content?.message || "处理失败";
  }
  if (type === "plan" || type === "confirm") {
    if (typeof content === "string") return { summary: content };
    return content || {};
  }
  if (type === "ask") {
    if (content && typeof content === "object") {
      return {
        kind: content.kind || "general",
        text: content.text || "",
        options: Array.isArray(content.options) ? content.options : [],
        items: Array.isArray(content.items) ? content.items : [],
      };
    }
    return { kind: "general", text: String(content || ""), options: [], items: [] };
  }
  return content;
}

/** 服务端历史 → 视图消息 */
function fromServer(message) {
  const type = message.type || "text";
  return {
    id: nextId(),
    role: message.role || "assistant",
    type,
    content: normalizeContent(type, message.content),
    toolCall: message.toolCall || null,
    turn: 0,
  };
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    sessionId: sessionStorage.getItem(SESSION_KEY) || null,
    sessions: [],
    messages: [],
    title: "新对话",
    busy: false,
    typing: false,
    canPause: false,
    // 流归属于哪条会话：与 sessionId 分离，用户切走后事件仍打到正确的流上
    streamSessionId: null,
    streamLastSeq: 0,
    pendingNewSession: false,
    openSeq: 0,
    turn: 0,
    turnTableId: null,
    abort: null,
    demo: false,
  }),

  getters: {
    empty: (state) => state.messages.length === 0,
    statusText(state) {
      if (!state.busy) return "";
      const events = useEventStore();
      if (events.mode === "generation") return "生成中";
      return state.canPause ? "回答中" : "处理中";
    },
    /** 事件只画在当前正在看的会话上 */
    viewing(state) {
      return state.sessionId === state.streamSessionId;
    },
  },

  actions: {
    // ---------------------------------------------------------------- 会话
    async bootstrap() {
      await this.loadSessions();
      if (!this.sessionId) return;
      try {
        await this.openSession(this.sessionId, { quiet: true });
      } catch (error) {
        this.newChat({ silent: true });
      }
    },

    async loadSessions() {
      try {
        this.sessions = (await api("GET", "/api/sessions")) || [];
      } catch (error) {
        if (error.status !== 401) useUiStore().toast("会话列表加载失败：" + error.message);
      }
    },

    newChat(options = {}) {
      const ui = useUiStore();
      if (this.demo && !options.silent) {
        ui.toast("演示模式只有一个示例会话，点左边那条会话即可");
        return;
      }
      if (this.busy && !options.silent) {
        ui.toast("正在回复中，完成后再开始新对话");
        return;
      }
      this.sessionId = null;
      sessionStorage.removeItem(SESSION_KEY);
      this.messages = [];
      this.title = "新对话";
      this.typing = false;
      this.turnTableId = null;
      this.pendingNewSession = false;
      useEventStore().reset();
      ui.closeSidebar();
    },

    async openSession(id, options = {}) {
      const ui = useUiStore();
      if (this.demo) {
        ui.toast("演示模式不加载历史会话，刷新页面即可回到示例");
        return;
      }
      if (this.busy && id === this.streamSessionId && !options.quiet) {
        ui.toast("该会话正在回复中，完成后再查看完整记录");
        return;
      }
      const seq = ++this.openSeq;
      let data;
      try {
        data = await api("GET", `/api/sessions/${id}/messages`);
      } catch (error) {
        if (seq !== this.openSeq) return;
        if (error.status === 404) {
          this.forgetSession(id);
          await this.loadSessions();
          this.newChat({ silent: true });
          ui.toast("会话不存在或已被删除");
          return;
        }
        ui.toast("会话加载失败：" + error.message);
        return;
      }
      if (seq !== this.openSeq) return;   // 快速连点：只认最后一次

      this.sessionId = id;
      sessionStorage.setItem(SESSION_KEY, id);
      this.pendingNewSession = false;
      this.turnTableId = null;
      this.typing = false;
      this.messages = (data.messages || []).map(fromServer);
      this.title = data.title || "对话";
      useEventStore().reset();
      ui.closeSidebar();

      const run = data.run || {};
      if (run.active && seq === this.openSeq) this.resume(id, run);
    },

    async removeSession(session) {
      const ui = useUiStore();
      if (this.demo) {
        ui.toast("演示模式不删除会话");
        return;
      }
      if (this.busy && session.sessionId === this.streamSessionId) {
        ui.toast("该会话正在回复中，完成后再删除");
        return;
      }
      const ok = await ui.ask(
        "删除会话",
        `确定删除「${session.title || "未命名会话"}」？会话记录与槽位会一并删除，不可恢复。`,
        "删除",
      );
      if (!ok) return;
      try {
        await api("DELETE", "/api/sessions/" + session.sessionId);
        if (this.sessionId === session.sessionId) this.newChat({ silent: true });
        await this.loadSessions();
        ui.toast("会话已删除");
      } catch (error) {
        ui.toast("删除失败：" + error.message);
      }
    },

    forgetSession(id) {
      if (this.sessionId === id) {
        this.sessionId = null;
        sessionStorage.removeItem(SESSION_KEY);
      }
    },

    adoptSession(id) {
      if (!id) return;
      // 流自己的 id 先记住：即使视图已经切走，断线续传也能打回正确的会话
      this.streamSessionId = id;
      if (!this.pendingNewSession || this.sessionId !== null) return;
      this.sessionId = id;
      sessionStorage.setItem(SESSION_KEY, id);
      this.pendingNewSession = false;
      if (this.title === "新对话") {
        const first = this.messages.find((m) => m.role === "user");
        if (first) this.title = String(first.content).slice(0, 20);
      }
    },

    // ---------------------------------------------------------------- 发送
    async send(text) {
      const ui = useUiStore();
      if (this.busy) {
        ui.toast("上一条仍在处理中，请稍候");
        return;
      }
      const message = String(text == null ? "" : text).trim();
      if (!message) return;

      const events = useEventStore();
      this.turn += 1;
      const turn = this.turn;
      this.pendingNewSession = this.sessionId === null;
      this.streamSessionId = this.sessionId;
      this.streamLastSeq = 0;
      this.turnTableId = null;
      this.messages.push({ id: nextId(), role: "user", type: "text", content: message, turn });
      events.reset();

      if (this.demo) {
        await this.demoReply(turn);
        return;
      }

      this.typing = true;
      this.busy = true;
      this.canPause = true;

      try {
        await openStream({
          message,
          sessionId: this.streamSessionId,
          afterSeq: 0,
          resume: false,
          token: useAuthStore().token,
          onEvent: (event) => this.applyEvent(event),
          onRetry: (note) => this.addNotice(note, turn),
        });
      } catch (error) {
        this.failStream(error);
      } finally {
        this.typing = false;
        this.busy = false;
        this.canPause = false;
        this.streamLastSeq = 0;
        events.finish();
        this.loadSessions();
      }
    },

    /** 演示模式：不连后端，回一句固定说明，避免现场演示时弹网络错误 */
    async demoReply(turn) {
      this.typing = true;
      this.busy = true;
      try {
        const { DEMO_REPLY } = await import("../demo/preview");
        await new Promise((resolve) => setTimeout(resolve, 700));
        this.messages.push({
          id: nextId(), role: "assistant", type: "text",
          content: DEMO_REPLY, turn,
        });
      } finally {
        this.typing = false;
        this.busy = false;
      }
    },

    /** 暂停本轮回答：受理即返回，终态从 paused 事件回来 */
    async pause() {
      const ui = useUiStore();
      if (!this.busy || !this.canPause) return;
      const sid = this.streamSessionId;
      if (!sid) {
        ui.toast("会话还在准备中，稍后再试");
        return;
      }
      try {
        await api("POST", "/api/chat/pause", { session_id: sid });
        ui.toast("正在暂停…");
      } catch (error) {
        ui.toast(error.message);
      }
    },

    async cancelGeneration() {
      const ui = useUiStore();
      const ok = await ui.ask(
        "取消生成",
        "确定取消本次生成？已完成步骤的产物会保留，需求不会上线。",
        "取消生成",
      );
      if (!ok) return;
      try {
        await api("POST", "/api/generate/cancel");
        ui.toast("取消请求已发出，将在当前步骤结束后停止");
      } catch (error) {
        ui.toast(error.message);
      }
    },

    /**
     * 刷新后接管一条仍在跑的会话。
     *
     * 不回放刷新前的事件——那些内容大半已在历史里，回放会看到两份；
     * 缺的部分在本轮结束时用一次 messages 拉取对账补齐。
     */
    async resume(id, run) {
      if (this.busy) return;
      const ui = useUiStore();
      const events = useEventStore();
      this.streamSessionId = id;
      this.streamLastSeq = run.seq || 0;
      this.pendingNewSession = false;
      this.canPause = run.mode !== "generation";
      if (run.mode === "generation") {
        events.start("generation");
      } else {
        this.typing = true;
      }
      this.addNotice("页面已重新连上，正在继续显示本次回答…", 0);
      this.busy = true;
      try {
        await openStream({
          message: "",
          sessionId: id,
          afterSeq: this.streamLastSeq,
          resume: true,
          token: useAuthStore().token,
          onEvent: (event) => this.applyEvent(event),
          onRetry: (note) => this.addNotice(note, 0),
        });
      } catch (error) {
        this.appendError("恢复连接失败：" + error.message);
        ui.toast("恢复连接失败：" + error.message);
      } finally {
        this.typing = false;
        this.canPause = false;
        this.busy = false;
        events.finish();
        await this.loadSessions();
        await this.reconcile(id);
      }
    },

    /** 以数据库为准对账：刷新/接管期间漏掉的事件在这里补齐 */
    async reconcile(id) {
      if (this.sessionId !== id || this.busy) return;
      let data;
      try {
        data = await api("GET", `/api/sessions/${id}/messages`);
      } catch (error) {
        return;   // 对账失败不打扰用户：页面上的内容至少是刷新前的历史
      }
      if (this.sessionId !== id || this.busy) return;
      this.messages = (data.messages || []).map(fromServer);
      this.title = data.title || this.title;
      this.turnTableId = null;
    },

    // ---------------------------------------------------------------- 事件
    applyEvent(event) {
      const events = useEventStore();
      const viewing = this.viewing;

      switch (event.type) {
        case "session":
          this.adoptSession(event.sessionId);
          break;
        case "tool_call":
          if (viewing) {
            this.typing = false;
            this.appendToolCall(event);
          }
          break;
        case "table":
          if (viewing) {
            this.typing = false;
            this.appendTable(event.table, event.replace !== false);
          }
          break;
        case "text":
          if (viewing) {
            this.typing = false;
            this.messages.push({
              id: nextId(), role: "assistant", type: "text",
              content: event.text || "", turn: this.turn,
            });
          }
          break;
        case "confirm":
          if (viewing) {
            this.typing = false;
            this.messages.push({
              id: nextId(), role: "assistant", type: "confirm",
              content: { summary: event.summary || event.text || "" }, turn: this.turn,
            });
          }
          break;
        case "capability":
          if (viewing) {
            this.typing = false;
            this.messages.push({
              id: nextId(), role: "assistant", type: "capability",
              content: { available: event.available || [], hint: event.hint || "" },
              turn: this.turn,
            });
          }
          break;
        case "ask":
          if (viewing) {
            this.typing = false;
            this.messages.push({
              id: nextId(), role: "assistant", type: "ask",
              content: {
                kind: event.kind || "general",
                text: event.text || "",
                options: Array.isArray(event.options) ? event.options : [],
                items: Array.isArray(event.items) ? event.items : [],
              },
              turn: this.turn,
            });
          }
          break;
        case "error":
          if (viewing) {
            this.typing = false;
            this.appendError(event.message || "处理失败");
          }
          break;
        case "paused":
          if (viewing) {
            this.typing = false;
            this.addNotice(event.text || "已暂停本次回答。可以继续提问，或补充条件后重新发送。");
          }
          break;
        case "phase":
          if (viewing) {
            this.typing = false;
            this.ensurePhaseMessage();
            if (events.mode !== "generation") events.start("generation");
            events.addStep(event);
            if (!event.ok) events.fail();
          }
          break;
        case "file":
          if (viewing) {
            this.typing = false;
            this.ensurePhaseMessage();
            if (events.mode !== "generation") events.start("generation");
            events.addFile(event.path);
          }
          break;
        case "log":
          if (viewing) {
            this.typing = false;
            this.ensurePhaseMessage();
            if (events.mode !== "generation") events.start("generation");
            events.addLog(event.text);
          }
          break;
        case "done":
          this.finishTurn(event);
          break;
        default:
          break;
      }
    },

    finishTurn(event) {
      const sid = event.sessionId;
      if (!sid) return;
      const wasPending = this.pendingNewSession;
      this.streamSessionId = sid;
      this.pendingNewSession = false;
      if (wasPending && this.sessionId === null) {
        this.sessionId = sid;
        sessionStorage.setItem(SESSION_KEY, sid);
      }
      useEventStore().finish();
      this.loadSessions();
    },

    /** 工具调用：同一轮里连续多次调用并成一条轨迹（不是一堆气泡） */
    appendToolCall(event) {
      const last = this.messages[this.messages.length - 1];
      const call = {
        name: event.name || "tool",
        ok: event.ok !== false,
        args: event.args || {},
        error: event.error || "",
        ms: event.ms ?? null,
      };
      if (last && last.type === "tools" && last.turn === this.turn) {
        last.calls.push(call);
        return;
      }
      this.messages.push({
        id: nextId(), role: "assistant", type: "tools",
        content: { calls: [call] }, turn: this.turn,
      });
    },

    /** 生成进度面板在消息流里的占位：同一轮只插一次，位置就是它出现的时刻 */
    ensurePhaseMessage() {
      const exists = this.messages.some((m) => m.type === "phase" && m.turn === this.turn);
      if (exists) return;
      this.messages.push({
        id: nextId(), role: "assistant", type: "phase", content: null, turn: this.turn,
      });
    },

    appendTable(table, replace) {
      if (!table || !Array.isArray(table.columns)) return;
      if (replace && this.turnTableId) {
        const existing = this.messages.find((m) => m.id === this.turnTableId);
        if (existing) {
          existing.content = table;
          existing.toolCall = table.toolCall || existing.toolCall;
          return;
        }
      }
      const id = nextId();
      this.messages.push({
        id, role: "assistant", type: "table", content: table,
        toolCall: table.toolCall || null, turn: this.turn,
      });
      this.turnTableId = id;
    },

    appendError(text) {
      this.messages.push({
        id: nextId(), role: "assistant", type: "error",
        content: text, turn: this.turn,
      });
    },

    addNotice(text, turn) {
      this.messages.push({
        id: nextId(), role: "assistant", type: "notice",
        content: text, turn: turn ?? this.turn,
      });
    },

    failStream(error) {
      const ui = useUiStore();
      if (error.status === 401) return;   // 已由 http 层统一回登录页
      if (error.status === 404) {
        this.forgetSession(this.streamSessionId);
        this.appendError("会话不存在或已被删除，请重新发起对话。");
        this.loadSessions();
        return;
      }
      const message = error instanceof HttpError ? error.message : String(error?.message || error);
      this.appendError(message);
      ui.toast(message);
    },
  },
});
