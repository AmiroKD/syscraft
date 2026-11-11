<script setup>
/**
 * 追问卡：系统向用户要信息时的界面。
 *
 * 三种形态对应三类追问（kind）：
 * - clarify（生成前的澄清）：逐项表单，答案拼成一条消息回传——抽取层的
 *   两轮协议不变，界面只是把「照着文案手打」变成「填空」；
 * - entity / record（指代消解）：候选项点选即发送，替用户把那句话敲出来；
 * - 兜底：只有正文，用户自己回复。
 *
 * 选项始终可点：追问发生很久以后（比如刷新回来）点一个候选项仍然是合理的动作。
 */
import { computed, ref } from "vue";
import AppIcon from "./AppIcon.vue";
import MarkdownView from "./MarkdownView.vue";
import { useChatStore } from "../stores/chat";
import { useUiStore } from "../stores/ui";

const props = defineProps({
  content: { type: Object, default: () => ({}) },
});

const chat = useChatStore();
const ui = useUiStore();

const TITLES = {
  clarify: "需要补充几个信息",
  entity: "需要指明操作对象",
  record: "需要指明哪一条记录",
  general: "需要你的输入",
};

const kind = computed(() => props.content?.kind || "general");
const title = computed(() => TITLES[kind.value] || TITLES.general);
const items = computed(() => props.content?.items || []);
const options = computed(() => props.content?.options || []);
const answers = ref([]);

function submit() {
  if (chat.busy) {
    ui.toast("上一条仍在处理中，请稍候");
    return;
  }
  const values = answers.value
    .map((value) => String(value == null ? "" : value).trim())
    .filter(Boolean);
  // 一项都没填 = 用户选择全按默认，与「全部按默认」按钮同一条路径
  chat.send(values.length ? values.join("；") : "按默认");
}

function send(text) {
  if (chat.busy) {
    ui.toast("上一条仍在处理中，请稍候");
    return;
  }
  chat.send(text);
}
</script>

<template>
  <div class="ask-card">
    <div class="card-title">
      <AppIcon name="help" />
      <span>{{ title }}</span>
    </div>

    <div v-if="items.length" class="form">
      <label v-for="(item, index) in items" :key="index" class="q-item">
        <span class="q-text">{{ index + 1 }}. {{ item.question }}</span>
        <input
          v-model="answers[index]"
          type="text"
          :placeholder="'默认：' + (item.default || '—')"
          autocomplete="off"
          @keydown.enter.prevent="submit"
        />
      </label>
      <div class="actions">
        <button class="btn-sm primary" type="button" :disabled="chat.busy" @click="submit">提交回答</button>
        <button class="btn-sm" type="button" :disabled="chat.busy" @click="send('按默认')">全部按默认</button>
      </div>
    </div>

    <template v-else>
      <div class="body"><MarkdownView :text="content?.text || ''" /></div>
      <div v-if="options.length" class="chips">
        <button
          v-for="option in options"
          :key="option.value"
          class="chip-btn"
          type="button"
          :disabled="chat.busy"
          @click="send(option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    </template>

    <p class="hint">也可以不点选项，直接在下方输入框用自己的话回复。</p>
  </div>
</template>

<style scoped>
.ask-card {
  border-radius: 13px;
  padding: 13px 15px;
  font-size: calc(13.5px * var(--scale));
  line-height: 1.7;
  border: 1px solid color-mix(in srgb, var(--accent) 26%, transparent);
  background: var(--accent-soft);
  max-width: 100%;
}
.card-title { margin-bottom: 6px; }
.card-title .i { color: var(--accent); }

/* 澄清表单 */
.form { display: flex; flex-direction: column; gap: 9px; margin-top: 4px; }
.q-item { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.q-text { font-size: 12.5px; color: var(--ink); }
.q-item input {
  width: 100%;
  height: 32px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: var(--surface);
  color: var(--ink);
  font-size: 12.5px;
  outline: none;
  transition: border-color .15s, box-shadow .15s;
}
.q-item input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.q-item input::placeholder { color: var(--ink-3); }

.actions { display: flex; gap: 8px; margin-top: 3px; flex-wrap: wrap; }

/* 候选点选 */
.chips { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 9px; }
.chip-btn {
  padding: 5px 12px;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, var(--accent) 30%, var(--line-strong));
  background: var(--surface);
  color: var(--accent);
  font-size: 12px;
  font-weight: 550;
  transition: background .12s, color .12s, border-color .12s;
}
.chip-btn:hover:not(:disabled) { background: var(--accent); color: var(--accent-ink); border-color: var(--accent); }
.chip-btn:disabled { opacity: .5; cursor: default; }

.hint { margin: 9px 0 0; font-size: 11px; color: var(--ink-3); }
</style>
