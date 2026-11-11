<script setup>
import { computed, nextTick, ref } from "vue";
import AppIcon from "./AppIcon.vue";
import { useChatStore } from "../stores/chat";
import { useSettingsStore } from "../stores/settings";
import { useUiStore } from "../stores/ui";

const emit = defineEmits(["send"]);

const chat = useChatStore();
const settings = useSettingsStore();
const ui = useUiStore();

const text = ref("");
const area = ref(null);

const pausable = computed(() => chat.busy && chat.canPause);
const hint = computed(() => (settings.sendKey === "mod"
  ? "⌘ + Enter 发送 · Enter 换行"
  : "Enter 发送 · Shift + Enter 换行"));

function resize() {
  const el = area.value;
  if (!el) return;
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 180) + "px";
}

function submit() {
  const value = text.value.trim();
  if (!value) return;
  if (chat.busy) {
    ui.toast("上一条仍在处理中，请稍候");
    return;
  }
  text.value = "";
  nextTick(resize);
  emit("send", value);
}

function onKeydown(event) {
  if (event.isComposing || event.keyCode === 229) return;   // 中文输入法组词中
  if (event.key !== "Enter") return;
  const mod = event.metaKey || event.ctrlKey;
  if (settings.sendKey === "enter" && !event.shiftKey && !mod) {
    event.preventDefault();
    submit();
  } else if (settings.sendKey === "mod" && mod) {
    event.preventDefault();
    submit();
  }
}

function pause() {
  chat.pause();
}
</script>

<template>
  <div class="composer-area">
    <form class="composer" :class="{ busy: chat.busy }" @submit.prevent="submit">
      <textarea
        ref="area"
        v-model="text"
        rows="1"
        maxlength="500"
        placeholder="给 SysCraft 发送消息…"
        autocomplete="off"
        aria-label="消息输入框"
        @input="resize"
        @keydown="onKeydown"
      ></textarea>

      <div class="bar">
        <span class="hint">{{ hint }}</span>
        <div class="actions">
          <button
            v-if="pausable"
            class="send is-pause"
            type="button"
            aria-label="暂停回答"
            title="暂停本次回答"
            @click="pause"
          >
            <AppIcon name="stop" />
          </button>
          <button
            v-else
            class="send"
            type="submit"
            aria-label="发送"
            title="发送"
            :disabled="chat.busy || !text.trim()"
          >
            <AppIcon name="arrow-up" />
          </button>
        </div>
      </div>
    </form>

    <div class="note">SysCraft 可能会出错，请核对重要数据。生成系统约需 2 分钟，期间查询不可用。</div>
  </div>
</template>

<style scoped>
.composer-area {
  flex: none;
  padding: 10px 24px 14px;
  background: linear-gradient(to bottom, transparent, var(--bg) 34%);
  transition: padding-bottom .25s ease;
}
.composer {
  max-width: 780px;
  margin: 0 auto;
  padding: 11px 11px 9px 16px;
  border: 1px solid var(--line-strong);
  border-radius: 18px;
  background: var(--surface);
  box-shadow: 0 8px 28px rgba(18, 20, 26, .07);
  transition: border-color .15s, box-shadow .15s;
}
html[data-theme="dark"] .composer { box-shadow: 0 10px 34px rgba(0, 0, 0, .35); }
.composer:focus-within {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--line-strong));
  box-shadow: 0 0 0 3px var(--accent-soft), 0 8px 28px rgba(18, 20, 26, .07);
}
textarea {
  display: block;
  width: 100%;
  min-height: 28px;
  max-height: 180px;
  padding: 3px 2px;
  border: 0;
  outline: 0;
  resize: none;
  background: transparent;
  color: var(--ink);
  font-size: calc(15px * var(--scale));
  line-height: 1.6;
  overflow-y: auto;
}
textarea::placeholder { color: var(--ink-3); }

.bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 6px; }
.hint { font-size: 11px; color: var(--ink-3); padding-left: 3px; }
.actions { display: flex; align-items: center; gap: 8px; flex: none; }

.send {
  width: 34px;
  height: 34px;
  flex: none;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--accent);
  color: var(--accent-ink);
  transition: background .15s, opacity .15s, transform .1s;
}
.send .i { width: 17px; height: 17px; stroke-width: 2.2; }
.send:hover { background: var(--accent-hover); }
.send:active { transform: scale(.96); }
.send:disabled { opacity: .3; cursor: default; transform: none; }

/* 暂停键与发送键同位：灰底而不是强调色——按下去是终止这一轮，不是又发一次 */
.send.is-pause { background: var(--surface-2); color: var(--ink-2); border: 1px solid var(--line-strong); }
.send.is-pause:hover { background: var(--surface-3); color: var(--ink); }
.send.is-pause .i { width: 15px; height: 15px; stroke-width: 2; fill: currentColor; }

.note { max-width: 780px; margin: 7px auto 0; text-align: center; font-size: 10.5px; color: var(--ink-3); }
</style>
