<script setup>
/**
 * 图表视图：ECharts 只在这一层出现。
 * 颜色全部从 CSS 变量读，换主题时重建 option——不写死色值，深浅色都不会
 * 出现「浅色格子配白色坐标轴」这种一眼假的东西。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import * as echarts from "echarts/core";
import { BarChart, LineChart, PieChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import AppIcon from "./AppIcon.vue";
import { buildChartOption, buildChartModel, CHART_TYPES } from "../lib/chart";
import { useSettingsStore } from "../stores/settings";

echarts.use([
  BarChart, LineChart, PieChart,
  GridComponent, LegendComponent, TooltipComponent,
  CanvasRenderer,
]);

const props = defineProps({
  table: { type: Object, required: true },
});

const settings = useSettingsStore();
const host = ref(null);
const type = ref("bar");

let chart = null;
let observer = null;

const model = computed(() => buildChartModel(props.table));
const types = computed(() => (model.value?.types || ["bar", "line"]));
const typeList = computed(() => CHART_TYPES.filter((item) => types.value.includes(item.value)));

const ICONS = { bar: "bars", line: "line", pie: "pie" };

function readTheme() {
  const styles = getComputedStyle(document.documentElement);
  const read = (name) => styles.getPropertyValue(name).trim();
  return {
    ink: read("--ink"),
    ink2: read("--ink-2"),
    ink3: read("--ink-3"),
    line: read("--line"),
    surface: read("--surface"),
  };
}

function render() {
  if (!host.value || !model.value) return;
  if (!chart) {
    chart = echarts.init(host.value, null, { renderer: "canvas" });
  }
  const option = buildChartOption(model.value, type.value, readTheme());
  if (option) chart.setOption(option, true);
}

onMounted(() => {
  if (!model.value) return;
  type.value = types.value.includes(model.value.defaultType)
    ? model.value.defaultType
    : types.value[0];
  render();
  observer = new ResizeObserver(() => chart?.resize());
  observer.observe(host.value);
});

onBeforeUnmount(() => {
  observer?.disconnect();
  chart?.dispose();
  chart = null;
});

watch(() => [props.table, type.value, settings.themeTick], render, { deep: false });

function pick(value) {
  type.value = value;
  render();
}
</script>

<template>
  <div v-if="!model" class="no-chart">这张表没有可用于画图的维度或度量</div>

  <div v-else class="chart-wrap">
    <div class="chart-bar">
      <span class="axis-hint">
        {{ model.dimension.title }}
        <template v-if="model.split"> × {{ model.split.title }}</template>
        <template v-if="model.measures.length"> / {{ model.measures.map((m) => m.title).join("、") }}</template>
      </span>
      <div class="segmented">
        <button
          v-for="item in typeList"
          :key="item.value"
          type="button"
          :class="{ on: type === item.value }"
          :title="item.label"
          @click="pick(item.value)"
        >
          <AppIcon :name="ICONS[item.value]" />
          {{ item.label }}
        </button>
      </div>
    </div>

    <div ref="host" class="chart-host"></div>
    <div v-if="model.note" class="chart-note">{{ model.note }}</div>
  </div>
</template>

<style scoped>
.no-chart { padding: 26px 14px; text-align: center; color: var(--ink-3); font-size: 12.5px; }

.chart-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 12px 2px;
}
.axis-hint {
  font-size: 11px;
  color: var(--ink-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.segmented button { display: inline-flex; align-items: center; gap: 4px; }
.segmented .i { width: 12px; height: 12px; }

.chart-host { width: 100%; height: 268px; }
.chart-note { padding: 0 12px 8px; font-size: 10.5px; color: var(--ink-3); }
</style>
