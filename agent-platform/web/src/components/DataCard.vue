<script setup>
/**
 * 数据卡：表格与图表是同一份数据的两种看法，所以合成一张卡、一个切换器。
 * 分成两张卡会让同一次查询在对话里出现两遍。
 */
import { computed, defineAsyncComponent, ref, watch } from "vue";
import AppIcon from "./AppIcon.vue";
import TableView from "./TableView.vue";
import { buildChartModel } from "../lib/chart";

const ChartView = defineAsyncComponent(() => import("./ChartView.vue"));

const props = defineProps({
  table: { type: Object, required: true },
  toolCall: { type: Object, default: null },
});

const LIMIT = 100;

const columns = computed(() => props.table.columns || []);
const rows = computed(() => props.table.rows || []);
const total = computed(() => (props.table.total == null ? rows.value.length : props.table.total));
const title = computed(() => props.table.display || props.table.entity || "查询结果");
const chartModel = computed(() => buildChartModel(props.table));

const view = ref(preferredView());

function preferredView() {
  if (!chartModel.value) return "table";
  // 聚合结果与小数据集先给图：这类问题的答案本来就是「形状」，不是逐行数字
  if (props.table.chart || props.table.kind === "aggregate") return "chart";
  return rows.value.length <= 12 ? "chart" : "table";
}

watch(chartModel, (model) => {
  if (!model && view.value === "chart") view.value = "table";
});

const hints = computed(() => {
  const list = [];
  if (columns.value.length > 5) list.push(`左右滑动查看全部 ${columns.value.length} 列`);
  if (rows.value.length > LIMIT) list.push(`仅展示前 ${LIMIT} 行（共 ${rows.value.length} 行）`);
  if (!rows.value.length && columns.value.length) list.push("没有符合条件的记录");
  return list;
});

const sourceTool = computed(() => {
  const name = props.toolCall?.name || props.table.toolCall?.name;
  if (!name) return "";
  const args = props.toolCall?.args || props.table.toolCall?.args || {};
  const entity = args.entity ? `（${args.entity}）` : "";
  return `由 ${name} 产生${entity}`;
});
</script>

<template>
  <div class="data-card card">
    <div class="head">
      <div class="head-left">
        <AppIcon :name="table.kind === 'aggregate' ? 'chart' : 'table'" class="head-ico" />
        <strong>{{ title }}</strong>
        <span class="count">共 {{ total }} 条</span>
      </div>
      <div v-if="chartModel" class="segmented">
        <button type="button" :class="{ on: view === 'table' }" @click="view = 'table'">表格</button>
        <button type="button" :class="{ on: view === 'chart' }" @click="view = 'chart'">图表</button>
      </div>
    </div>

    <div v-if="!columns.length || !rows.length" class="empty">
      {{ columns.length ? "没有符合条件的记录" : "这条历史记录只保留了统计信息" }}
    </div>

    <template v-else>
      <TableView v-if="view === 'table'" :table="table" />
      <ChartView v-else :table="table" />
    </template>

    <div v-if="hints.length || sourceTool" class="foot">
      <span v-if="hints.length">{{ hints.join(" · ") }}</span>
      <span v-if="sourceTool" class="source">{{ sourceTool }}</span>
    </div>
  </div>
</template>

<style scoped>
.data-card { margin: 2px 0 4px; }

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px 8px 14px;
  border-bottom: 1px solid var(--line);
  background: var(--bg-soft);
  font-size: 12px;
  color: var(--ink-2);
}
.head-left { display: flex; align-items: center; gap: 7px; min-width: 0; }
.head-ico { width: 14px; height: 14px; color: var(--ink-3); }
.head-left strong {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.count { color: var(--ink-3); flex: none; }
.segmented { padding: 2px; }
.segmented button { padding: 3px 9px; font-size: 11.5px; }

.empty { padding: 22px 14px; text-align: center; color: var(--ink-3); font-size: 12.5px; }

.foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 7px 14px;
  font-size: 11.5px;
  color: var(--ink-3);
  border-top: 1px solid var(--line);
}
.source { font-family: var(--mono); font-size: 10.5px; }
</style>
