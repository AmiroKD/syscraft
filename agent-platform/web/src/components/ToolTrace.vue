<script setup>
/**
 * 工具调用轨迹。
 *
 * 一次提问里模型可能连续调用多个工具（含试错），它们不是「回答」，所以做成
 * 一条可折叠的轨迹条，而不是一串助手气泡——回答被工具日志埋掉是最难读的界面。
 *
 * 参数只展示「能对上业务语言」的那部分：实体、条件、分组、条数。
 * 完整 JSON 放在 title 里，需要时悬停看。
 */
import { computed, ref } from "vue";
import AppIcon from "./AppIcon.vue";

const props = defineProps({
  calls: { type: Array, default: () => [] },
});

const open = ref(true);

const TOOL_LABELS = {
  query_records: "查询记录",
  aggregate_records: "分组统计",
  insert_records: "新增记录",
  update_records: "修改记录",
  delete_records: "删除记录",
};

const failedCount = computed(() => props.calls.filter((c) => !c.ok).length);

function label(name) {
  return TOOL_LABELS[name] || name;
}

function short(value, limit = 60) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (!text) return "";
  return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
}

/** 把工具参数翻成「人话」的几段，没有可说的就不显示 */
function details(call) {
  const args = call.args || {};
  const out = [];
  if (args.entity) out.push({ label: "实体", value: short(args.entity) });
  if (args.filter && Object.keys(args.filter).length) {
    out.push({ label: "条件", value: short(args.filter, 48) });
  }
  if (Array.isArray(args.groupBy) && args.groupBy.length) {
    out.push({ label: "分组", value: args.groupBy.join("、") });
  }
  if (Array.isArray(args.metrics) && args.metrics.length) {
    out.push({
      label: "指标",
      value: args.metrics.map((m) => `${m.func || m.function}${m.field ? "(" + m.field + ")" : "(*)"}`
        + (m.alias ? "→" + m.alias : "")).join("、"),
    });
  }
  if (args.orderBy) out.push({ label: "排序", value: short(args.orderBy, 32) });
  if (args.size) out.push({ label: "条数", value: String(args.size) });
  if (args.id) out.push({ label: "记录", value: "#" + args.id });
  if (args.row && typeof args.row === "object") {
    out.push({ label: "写入", value: short(Object.keys(args.row).join("、"), 40) });
  }
  if (args.values && typeof args.values === "object") {
    out.push({ label: "修改", value: short(Object.keys(args.values).join("、"), 40) });
  }
  return out;
}

function duration(call) {
  if (call.ms == null) return "";
  if (call.ms < 1000) return `${Math.round(call.ms)} ms`;
  return `${(call.ms / 1000).toFixed(1)} s`;
}

function rawJson(call) {
  try {
    return JSON.stringify(call.args || {}, null, 2);
  } catch (e) {
    return "";
  }
}
</script>

<template>
  <div class="trace" :class="{ fail: failedCount }">
    <button class="trace-head" type="button" :aria-expanded="open" @click="open = !open">
      <AppIcon name="terminal" class="trace-ico" />
      <span class="trace-title">工具调用</span>
      <span class="trace-count">{{ calls.length }} 次</span>
      <span v-if="failedCount" class="trace-fail">{{ failedCount }} 次失败</span>
      <AppIcon name="chevron" class="chevron" :class="{ open }" />
    </button>

    <div v-if="open" class="trace-body">
      <div v-for="(call, index) in calls" :key="index" class="call" :class="{ bad: !call.ok }">
        <span class="dot" aria-hidden="true"></span>
        <div class="call-main">
          <div class="call-line">
            <strong>{{ label(call.name) }}</strong>
            <code class="raw-name">{{ call.name }}</code>
            <span v-for="item in details(call)" :key="item.label" class="chip" :title="item.value">
              <em>{{ item.label }}</em>{{ item.value }}
            </span>
            <span v-if="duration(call)" class="ms">{{ duration(call) }}</span>
            <span class="status" :title="call.ok ? '成功' : '失败'">
              {{ call.ok ? "成功" : "失败" }}
            </span>
          </div>
          <div v-if="!call.ok && call.error" class="call-error">{{ call.error }}</div>
        </div>
        <span class="json" :title="rawJson(call)">参数</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.trace {
  border: 1px solid var(--line);
  border-radius: 11px;
  background: var(--bg-soft);
  overflow: hidden;
}
.trace.fail { border-color: color-mix(in srgb, var(--danger) 26%, var(--line)); }

.trace-head {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  padding: 7px 11px;
  text-align: left;
  font-size: 11.5px;
  color: var(--ink-2);
  transition: background .12s;
}
.trace-head:hover { background: var(--surface-2); }
.trace-ico { width: 13px; height: 13px; color: var(--ink-3); }
.trace-title { font-weight: 600; color: var(--ink-2); }
.trace-count { color: var(--ink-3); }
.trace-fail { color: var(--danger); }
.chevron { margin-left: auto; width: 13px; height: 13px; color: var(--ink-3); transition: transform .18s; }
.chevron.open { transform: rotate(90deg); }

.trace-body { padding: 2px 11px 8px; display: flex; flex-direction: column; gap: 6px; }
.call { display: flex; gap: 8px; align-items: flex-start; }
.dot {
  width: 6px;
  height: 6px;
  margin-top: 6px;
  flex: none;
  border-radius: 50%;
  background: var(--ok);
}
.call.bad .dot { background: var(--danger); }

.call-main { min-width: 0; flex: 1; }
.call-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--ink-2);
}
.call-line strong { font-size: 12.5px; font-weight: 600; color: var(--ink); }
.raw-name {
  font: 10.5px/1.6 var(--mono);
  color: var(--ink-3);
  background: var(--surface-2);
  border-radius: 4px;
  padding: 0 4px;
}
.chip {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  max-width: 320px;
  padding: 0 6px;
  border-radius: 5px;
  background: var(--surface);
  border: 1px solid var(--line);
  font-size: 11px;
  color: var(--ink-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.chip em { font-style: normal; color: var(--ink-3); }
.ms { font-size: 10.5px; color: var(--ink-3); }
.status { margin-left: auto; font-size: 10.5px; color: var(--ok); }
.call.bad .status { color: var(--danger); }

.call-error {
  margin-top: 2px;
  font-size: 11.5px;
  line-height: 1.6;
  color: var(--danger);
  background: var(--danger-soft);
  border-radius: 6px;
  padding: 4px 7px;
}
.json {
  flex: none;
  font-size: 10px;
  color: var(--ink-3);
  border-bottom: 1px dashed var(--line-strong);
  cursor: help;
}
</style>
