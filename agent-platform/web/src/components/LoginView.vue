<script setup>
import { ref } from "vue";
import { useAuthStore } from "../stores/auth";

const emit = defineEmits(["success"]);

const auth = useAuthStore();
const username = ref("");
const password = ref("");
const error = ref("");
const busy = ref(false);
const passwordEl = ref(null);

async function submit() {
  if (busy.value) return;
  error.value = "";
  if (!username.value.trim() || !password.value) {
    error.value = "请输入用户名和密码";
    return;
  }
  busy.value = true;
  try {
    await auth.login(username.value.trim(), password.value);
    emit("success");
  } catch (e) {
    error.value = e.message || "登录失败";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="login-view">
    <div class="glow glow-a"></div>
    <div class="glow glow-b"></div>

    <form class="login-card" novalidate @submit.prevent="submit">
      <div class="brand">
        <span class="brand-mark brand-mark-lg">S</span>
        <div>
          <div class="title">SysCraft</div>
          <div class="sub">语构 · 用语言构建系统</div>
        </div>
      </div>

      <p class="lead">说一句话查数据；描述一个需求，生成一套可运行的系统。</p>

      <label class="field">
        <span>用户名</span>
        <input
          v-model="username"
          autocomplete="username"
          placeholder="请输入用户名"
          @keydown.enter.prevent="passwordEl?.focus()"
        />
      </label>

      <label class="field">
        <span>密码</span>
        <input
          ref="passwordEl"
          v-model="password"
          type="password"
          autocomplete="current-password"
          placeholder="请输入密码"
        />
      </label>

      <div class="error" role="alert">{{ error }}</div>

      <button class="btn-primary submit" type="submit" :disabled="busy">
        {{ busy ? "登录中…" : "登 录" }}
      </button>

      <div class="foot">账号与权限由生成系统的产物侧统一管理</div>
    </form>
  </div>
</template>

<style scoped>
.login-view {
  position: fixed;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 24px;
  background:
    radial-gradient(1100px 500px at 15% -10%, var(--accent-soft), transparent 60%),
    var(--bg-soft);
  overflow: hidden;
}
.glow {
  position: absolute;
  width: 420px;
  height: 420px;
  border-radius: 50%;
  filter: blur(90px);
  opacity: .5;
  pointer-events: none;
}
.glow-a { background: #b9ccff; top: -140px; left: -100px; }
.glow-b { background: #d8c7ff; bottom: -180px; right: -120px; opacity: .42; }
html[data-theme="dark"] .glow-a { background: #24406e; }
html[data-theme="dark"] .glow-b { background: #3a2a5e; }

.login-card {
  position: relative;
  z-index: 1;
  width: min(400px, 100%);
  background: color-mix(in srgb, var(--surface) 92%, transparent);
  border: 1px solid var(--line);
  border-radius: 20px;
  padding: 30px 30px 24px;
  box-shadow: var(--shadow-lg);
  backdrop-filter: blur(12px);
}
.brand { display: flex; align-items: center; gap: 12px; margin-bottom: 18px; }
.title { font-size: calc(22px * var(--scale)); font-weight: 700; letter-spacing: -.02em; }
.sub { font-size: 12px; color: var(--ink-3); }
.lead { color: var(--ink-2); font-size: 13px; margin: 0 0 22px; }

.field { display: block; margin-bottom: 14px; }
.field > span { display: block; font-size: 12px; color: var(--ink-2); margin-bottom: 6px; }
.field input {
  width: 100%;
  padding: 11px 13px;
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  background: var(--bg);
  outline: none;
  transition: border-color .15s, box-shadow .15s;
}
.field input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-soft);
}
.error { min-height: 20px; font-size: 12px; color: var(--danger); margin: 2px 0 8px; }
.submit { width: 100%; }
.foot { margin-top: 16px; text-align: center; font-size: 11.5px; color: var(--ink-3); }
</style>
