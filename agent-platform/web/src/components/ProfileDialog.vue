<script setup>
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import BaseDialog from "./BaseDialog.vue";
import { useAuthStore } from "../stores/auth";
import { useUiStore } from "../stores/ui";

const auth = useAuthStore();
const ui = useUiStore();

const open = computed(() => ui.dialog === "profile");
</script>

<template>
  <BaseDialog :open="open" @close="ui.closeDialog()">
    <div class="modal-head">
      <div>
        <h2>个人资料</h2>
        <p>账号来自生成系统产物侧的鉴权服务</p>
      </div>
      <button class="icon-btn" type="button" aria-label="关闭" @click="ui.closeDialog()">
        <AppIcon name="x" />
      </button>
    </div>
    <div class="modal-body">
      <div class="hero">
        <span class="avatar avatar-lg">{{ auth.initial }}</span>
        <div>
          <strong>{{ auth.name }}</strong>
          <small>{{ auth.roleLabel }}</small>
        </div>
      </div>
      <div class="kv-list">
        <div><span>用户名</span><b>{{ auth.me?.username || "—" }}</b></div>
        <div><span>角色编码</span><b>{{ auth.me?.roleCode || "—" }}</b></div>
        <div><span>用户 ID</span><b>{{ auth.me?.id ?? "—" }}</b></div>
      </div>
      <p class="muted">平台不存储密码；令牌仅在当前标签页的 sessionStorage 中保留，关闭页面即失效。</p>
    </div>
    <div class="modal-foot">
      <button class="btn-primary" type="button" @click="ui.closeDialog()">知道了</button>
    </div>
  </BaseDialog>
</template>

<style scoped>
.hero { display: flex; align-items: center; gap: 14px; padding: 16px 0 6px; }
.hero > div { display: flex; flex-direction: column; line-height: 1.4; }
.hero strong { font-size: 15px; font-weight: 620; }
.hero small { font-size: 11.5px; color: var(--ink-3); }
</style>
