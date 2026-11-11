<script setup>
/**
 * 生成进度面板：阶段条 + 产物文件 + 编译日志。
 * 它是一条消息里的「过程」，不是一堆消息——所以三块收在一张卡里。
 */
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import { useChatStore } from "../stores/chat";
import { useEventStore } from "../stores/event";

const events = useEventStore();
const chat = useChatStore();

const title = computed(() => {
  if (events.failed) return "生成未完成";
  return events.running ? "正在生成系统…" : "生成结束";
});

const visibleFiles = computed(() => (
  events.filesOpen ? events.files : events.files.slice(0, 6)
));
const visibleLogs = computed(() => (
  events.logsOpen ? events.logs.slice(-30) : events.logs.slice(-6)
));
</script>

<template>
  <div class="phase-card" :class="{ failed: events.failed }">
    <div class="head">
      <span v-if="events.running" class="spinner"></span>
      <span v-else class="done-mark" :class="{ fail: events.failed }">{{ events.failed ? "✗" : "✓" }}</span>
      <span class="title">{{ title }}</span>
      <button
        v-if="events.running"
        class="btn-sm danger cancel"
        type="button"
        @click="chat.cancelGeneration()"
      >
        取消生成
      </button>
    </div>

    <p v-if="events.running" class="tip">
      全程约 2 分钟，期间查询不可用；可随时取消，已完成步骤的产物会保留。
    </p>

    <div v-if="events.steps.length" class="steps">
      <div v-for="(step, index) in events.steps" :key="index" class="step-line">
        <span class="mark" :class="step.ok ? 'ok' : 'fail'">{{ step.ok ? "✓" : "✗" }}</span>
        <span class="step-name">{{ step.step }}</span>
        <span class="step-detail">{{ step.detail }}</span>
      </div>
    </div>

    <div v-if="events.files.length" class="block">
      <button class="block-head" type="button" @click="events.filesOpen = !events.filesOpen">
        <AppIcon name="layers" />
        <span>产物文件</span>
        <em>{{ events.files.length }}</em>
        <AppIcon name="chevron" class="chev" :class="{ open: events.filesOpen }" />
      </button>
      <div class="file-list">
        <div v-for="file in visibleFiles" :key="file" class="file">{{ file }}</div>
        <button
          v-if="!events.filesOpen && events.files.length > 6"
          class="more"
          type="button"
          @click="events.filesOpen = true"
        >
          …等 {{ events.files.length }} 个文件，点击展开
        </button>
      </div>
    </div>

    <div v-if="events.logs.length" class="block">
      <button class="block-head" type="button" @click="events.logsOpen = !events.logsOpen">
        <AppIcon name="terminal" />
        <span>编译 / 修复日志</span>
        <em>{{ events.logs.length }}</em>
        <AppIcon name="chevron" class="chev" :class="{ open: events.logsOpen }" />
      </button>
      <div class="log-list">
        <div v-for="(line, index) in visibleLogs" :key="index">{{ line }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.phase-card {
  border: 1px solid var(--line);
  border-radius: 13px;
  background: var(--bg-soft);
  padding: 12px 14px;
  font-size: calc(13px * var(--scale));
}
.phase-card.failed { border-color: color-mix(in srgb, var(--danger) 30%, transparent); }

.head { display: flex; align-items: center; gap: 9px; }
.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--line-strong);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin .8s linear infinite;
  flex: none;
}
.done-mark { font-weight: 700; color: var(--ok); font-size: 13px; }
.done-mark.fail { color: var(--danger); }
.title { font-weight: 600; font-size: 13px; }
.cancel { margin-left: auto; }

.tip { color: var(--ink-3); font-size: 11.5px; margin: 3px 0 8px 23px; }

.steps { display: flex; flex-direction: column; gap: 3px; }
.step-line { display: flex; gap: 7px; align-items: baseline; font-size: 12.5px; color: var(--ink-2); }
.mark { font-weight: 700; width: 12px; flex: none; }
.mark.ok { color: var(--ok); }
.mark.fail { color: var(--danger); }
.step-name { color: var(--ink); }
.step-detail { color: var(--ink-3); font-size: 11.5px; }

.block { margin-top: 10px; border-top: 1px dashed var(--line); padding-top: 8px; }
.block-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  font-size: 11px;
  color: var(--ink-3);
  text-align: left;
}
.block-head .i { width: 12px; height: 12px; }
.block-head em { font-style: normal; }
.block-head .chev { margin-left: auto; transition: transform .18s; }
.block-head .chev.open { transform: rotate(90deg); }

.file-list { margin-top: 5px; font-size: 11.5px; color: var(--ink-2); line-height: 1.75; max-height: 150px; overflow: auto; }
.file { font-family: var(--mono); font-size: 11px; }
.more { color: var(--accent); font-size: 11px; }

.log-list {
  margin-top: 5px;
  font: 11px/1.65 var(--mono);
  color: var(--ink-2);
  background: var(--code-bg);
  border-radius: 7px;
  padding: 7px 9px;
  max-height: 150px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
