<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import AppIcon from "./AppIcon.vue";
import { useAuthStore } from "../stores/auth";
import { useChatStore } from "../stores/chat";
import { useUiStore } from "../stores/ui";

const auth = useAuthStore();
const chat = useChatStore();
const ui = useUiStore();

const menuOpen = ref(false);
const footEl = ref(null);

function groupOf(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "更早";
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diffDays = Math.floor((startOfToday - day) / 86400000);
  if (diffDays <= 0) return "今天";
  if (diffDays === 1) return "昨天";
  if (diffDays <= 7) return "7 天内";
  if (diffDays <= 30) return "30 天内";
  return "更早";
}

const groups = computed(() => {
  const map = new Map();
  for (const session of chat.sessions) {
    const label = groupOf(session.lastActiveAt);
    if (!map.has(label)) map.set(label, []);
    map.get(label).push(session);
  }
  return [...map.entries()];
});

function openSession(id) {
  menuOpen.value = false;
  chat.openSession(id);
}

function onDocumentClick(event) {
  if (menuOpen.value && footEl.value && !footEl.value.contains(event.target)) {
    menuOpen.value = false;
  }
}

function menuAction(action) {
  menuOpen.value = false;
  if (action === "profile") ui.openDialog("profile");
  if (action === "admin") ui.openDialog("admin");
  if (action === "settings") ui.openDialog("settings");
  if (action === "help") ui.openDialog("help");
  if (action === "logout") {
    chat.abortStream();   // 先断流再清状态，避免旧连接继续打事件
    auth.logout();
    chat.newChat({ silent: true });
    chat.sessions = [];
  }
}

onMounted(() => document.addEventListener("click", onDocumentClick));
onBeforeUnmount(() => document.removeEventListener("click", onDocumentClick));
</script>

<template>
  <div class="scrim" :class="{ show: ui.sidebarOpen }" @click="ui.closeSidebar()"></div>

  <aside class="sidebar" :class="{ open: ui.sidebarOpen }">
    <div class="brand-row">
      <div class="brand">
        <span class="brand-mark">S</span>
        <div class="brand-text">
          <strong>SysCraft</strong>
          <small>语构 · 自然语言构建系统</small>
        </div>
      </div>
    </div>

    <div class="sidebar-body">
      <button class="new-chat" type="button" @click="chat.newChat()">
        <AppIcon name="plus" />
        <span>新对话</span>
        <kbd>⌘K</kbd>
      </button>

      <nav class="session-list" aria-label="历史会话">
        <div v-if="!chat.sessions.length" class="session-empty">
          还没有历史会话。上方「新对话」开始第一次提问。
        </div>
        <template v-for="[label, items] in groups" :key="label">
          <div class="group-label">{{ label }}</div>
          <div
            v-for="session in items"
            :key="session.sessionId"
            class="session"
            :class="{ active: session.sessionId === chat.sessionId }"
            role="button"
            tabindex="0"
            :aria-label="'打开会话：' + (session.title || '未命名')"
            @click="openSession(session.sessionId)"
            @keydown.enter.prevent="openSession(session.sessionId)"
            @keydown.space.prevent="openSession(session.sessionId)"
          >
            <span class="title">{{ session.title || "未命名会话" }}</span>
            <button
              class="del"
              type="button"
              title="删除会话"
              aria-label="删除会话"
              @click.stop="chat.removeSession(session)"
            >
              <AppIcon name="trash" />
            </button>
          </div>
        </template>
      </nav>
    </div>

    <div ref="footEl" class="sidebar-foot">
      <transition name="pop">
        <div v-if="menuOpen" class="popover" role="menu">
          <div class="menu-user">
            <span class="avatar">{{ auth.initial }}</span>
            <div class="menu-user-meta">
              <strong>{{ auth.name }}</strong>
              <small>{{ auth.roleLabel }}</small>
            </div>
          </div>
          <div class="menu-sep"></div>
          <button class="menu-item" type="button" role="menuitem" @click="menuAction('profile')">
            <AppIcon name="user" /><span>个人资料</span>
          </button>
          <button v-if="auth.isAdmin" class="menu-item" type="button" role="menuitem" @click="menuAction('admin')">
            <AppIcon name="shield" /><span>账号管理</span>
          </button>
          <button class="menu-item" type="button" role="menuitem" @click="menuAction('settings')">
            <AppIcon name="settings" /><span>设置</span>
          </button>
          <button class="menu-item" type="button" role="menuitem" @click="menuAction('help')">
            <AppIcon name="help" /><span>帮助</span>
          </button>
          <div class="menu-sep"></div>
          <button class="menu-item danger" type="button" role="menuitem" @click="menuAction('logout')">
            <AppIcon name="logout" /><span>退出登录</span>
          </button>
        </div>
      </transition>

      <button class="user-chip" type="button" :aria-expanded="menuOpen" @click.stop="menuOpen = !menuOpen">
        <span class="avatar">{{ auth.initial }}</span>
        <span class="user-meta">
          <strong>{{ auth.name }}</strong>
          <small>{{ auth.roleLabel }}</small>
        </span>
        <span class="dots" aria-hidden="true">⋯</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  width: 264px;
  flex: 0 0 264px;
  display: flex;
  flex-direction: column;
  background: var(--sidebar);
  border-right: 1px solid var(--line);
  min-height: 0;
  z-index: 20;
}

.brand-row { padding: 14px 14px 8px; }
.brand { display: flex; align-items: center; gap: 10px; min-width: 0; }
.brand-text { display: flex; flex-direction: column; line-height: 1.25; min-width: 0; }
.brand-text strong { font-size: 14.5px; letter-spacing: -.01em; }
.brand-text small {
  font-size: 10.5px;
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sidebar-body { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 6px 10px 10px; }

.new-chat {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 9px 11px;
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--surface);
  box-shadow: var(--shadow-sm);
  font-size: 13.5px;
  font-weight: 550;
  transition: border-color .15s, box-shadow .15s, transform .1s;
}
.new-chat:hover { border-color: var(--line-strong); box-shadow: var(--shadow-md); }
.new-chat:active { transform: translateY(1px); }
.new-chat .i { color: var(--ink-2); }
.new-chat kbd {
  margin-left: auto;
  font-family: var(--font);
  font-size: 10.5px;
  color: var(--ink-3);
  background: var(--surface-2);
  border: 1px solid var(--line);
  border-radius: 5px;
  padding: 1px 5px;
}

.session-list { flex: 1; min-height: 0; overflow-y: auto; padding: 6px 0 10px; margin: 0 -2px; }
.group-label {
  padding: 14px 10px 5px;
  font-size: 10.5px;
  font-weight: 600;
  letter-spacing: .05em;
  color: var(--ink-3);
  user-select: none;
}
.session {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 9px;
  font-size: 13.5px;
  color: var(--ink-2);
  cursor: pointer;
  user-select: none;
  transition: background .12s, color .12s;
}
.session:hover { background: var(--surface-2); color: var(--ink); }
.session.active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: inset 0 0 0 1px var(--line), var(--shadow-sm);
}
.session .title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.session .del {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  border-radius: 6px;
  color: var(--ink-3);
  opacity: 0;
  transition: opacity .12s, background .12s, color .12s;
}
.session:hover .del, .session.active .del { opacity: 1; }
.session .del:hover { background: var(--danger-soft); color: var(--danger); }
.session .del :deep(.i) { width: 14px; height: 14px; }
.session-empty { padding: 18px 10px; font-size: 12px; color: var(--ink-3); line-height: 1.7; }

.sidebar-foot { position: relative; padding: 8px 10px 10px; border-top: 1px solid var(--line); }
.user-chip {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 8px;
  border-radius: 11px;
  text-align: left;
  transition: background .12s;
}
.user-chip:hover, .user-chip[aria-expanded="true"] { background: var(--surface-2); }
.user-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.3; }
.user-meta strong { font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.user-meta small { font-size: 10.5px; color: var(--ink-3); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.dots { color: var(--ink-3); font-size: 15px; line-height: 1; padding: 0 2px; }

.popover {
  position: absolute;
  left: 10px;
  right: 10px;
  bottom: calc(100% + 6px);
  padding: 6px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 14px;
  box-shadow: var(--shadow-lg);
  transform-origin: bottom center;
  z-index: 40;
}
.pop-enter-active, .pop-leave-active { transition: opacity .13s ease, transform .13s ease; }
.pop-enter-from, .pop-leave-to { opacity: 0; transform: translateY(6px) scale(.98); }
.menu-user { display: flex; gap: 10px; align-items: center; padding: 9px 9px 10px; }
.menu-user-meta { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; }
.menu-user-meta strong { font-size: 13px; }
.menu-user-meta small { font-size: 11px; color: var(--ink-3); }
.menu-sep { height: 1px; background: var(--line); margin: 5px 6px; }
.menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 9px;
  border-radius: 9px;
  font-size: 13px;
  color: var(--ink);
  text-align: left;
  transition: background .12s;
}
.menu-item .i { color: var(--ink-2); }
.menu-item:hover { background: var(--surface-2); }
.menu-item.danger { color: var(--danger); }
.menu-item.danger .i { color: var(--danger); }
.menu-item.danger:hover { background: var(--danger-soft); }

.scrim { display: none; }

@media (max-width: 860px) {
  .sidebar {
    position: fixed;
    inset: 0 auto 0 0;
    transform: translateX(-100%);
    transition: transform .22s ease;
    box-shadow: var(--shadow-lg);
  }
  .sidebar.open { transform: none; }
  .scrim.show {
    display: block;
    position: fixed;
    inset: 0;
    background: rgba(17, 19, 24, .38);
    z-index: 15;
  }
}
</style>
