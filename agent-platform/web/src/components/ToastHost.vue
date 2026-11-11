<script setup>
import { useUiStore } from "../stores/ui";

const ui = useUiStore();
</script>

<template>
  <div class="toast-host" role="status" aria-live="polite">
    <transition-group name="toast">
      <div v-for="item in ui.toasts" :key="item.id" class="toast" :class="item.tone">
        {{ item.message }}
      </div>
    </transition-group>
  </div>
</template>

<style scoped>
.toast-host {
  position: fixed;
  left: 50%;
  bottom: 26px;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  z-index: 90;
  pointer-events: none;
}
.toast {
  max-width: min(520px, calc(100vw - 48px));
  padding: 9px 15px;
  border-radius: 10px;
  background: rgba(24, 26, 32, .94);
  color: #fff;
  font-size: 12.5px;
  line-height: 1.6;
  box-shadow: var(--shadow-lg);
  animation: toast-in .18s ease-out;
}
html[data-theme="dark"] .toast { background: #f2f3f5; color: #16171a; }

.toast-enter-active, .toast-leave-active { transition: opacity .18s ease, transform .18s ease; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(6px); }

@keyframes toast-in {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: none; }
}
</style>
