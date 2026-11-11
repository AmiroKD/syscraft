<script setup>
import { onMounted } from "vue";
import { onUnauthorized } from "./lib/http";
import { useAuthStore } from "./stores/auth";
import { useChatStore } from "./stores/chat";
import { useSettingsStore } from "./stores/settings";
import { useUiStore } from "./stores/ui";
import IconSprite from "./components/IconSprite.vue";
import LoginView from "./components/LoginView.vue";
import AppSidebar from "./components/AppSidebar.vue";
import ChatView from "./components/ChatView.vue";
import SettingsDialog from "./components/SettingsDialog.vue";
import ProfileDialog from "./components/ProfileDialog.vue";
import HelpDialog from "./components/HelpDialog.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import ToastHost from "./components/ToastHost.vue";

const auth = useAuthStore();
const chat = useChatStore();
const settings = useSettingsStore();
const ui = useUiStore();

const demoMode = new URLSearchParams(window.location.search).has("demo");

async function enterApp() {
  if (chat.demo) return;
  await chat.bootstrap();
  settings.loadServerStatus();
}

onMounted(async () => {
  settings.applyTheme();
  settings.watchSystemTheme();

  // 401 只有一条出口：清状态回登录页。不做静默刷新——那会掩盖「权限已被改动」
  onUnauthorized(() => {
    auth.logout();
    chat.newChat({ silent: true });
    chat.sessions = [];
    ui.toast("登录状态已失效，请重新登录");
  });

  if (demoMode) {
    const { seedDemo } = await import("./demo/preview");
    auth.booting = false;
    auth.me = { id: 0, username: "演示账号", roleCode: "SUPER_ADMIN", roleName: "超级管理员" };
    chat.demo = true;
    seedDemo(chat);
    return;
  }

  await auth.restore();
  if (auth.loggedIn) await enterApp();
});
</script>

<template>
  <IconSprite />

  <LoginView v-if="!auth.booting && !auth.loggedIn" @success="enterApp" />

  <div v-else-if="auth.loggedIn" class="app">
    <AppSidebar />
    <ChatView />
  </div>

  <div v-else class="boot">
    <span class="boot-spinner"></span>
  </div>

  <SettingsDialog />
  <ProfileDialog />
  <HelpDialog />
  <ConfirmDialog />
  <ToastHost />
</template>

<style scoped>
.app { display: flex; height: 100%; background: var(--bg); }

.boot {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  background: var(--bg-soft);
}
.boot-spinner {
  width: 22px;
  height: 22px;
  border: 2px solid var(--line-strong);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin .8s linear infinite;
}
</style>
