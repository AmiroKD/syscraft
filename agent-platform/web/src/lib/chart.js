/**
 * 表格 → 图表。
 *
 * 判断权在后端：聚合类结果带 `table.chart = {dimensions, measures}` 元数据，
 * 前端照它画；普通明细表没有元数据，就按「列类型」自己推断一版
 * （非主键数字列当度量、文本/时间列当维度）。
 *
 * 这里只产出纯数据 + ECharts option，不引 echarts 本体——让图表逻辑可单测，
 * 也让 echarts 只在真正需要画图时才加载。
 */
import {
  compactNumber,
  dimensionLabel,
  formatNumber,
  isIdColumn,
  isNumericColumn,
  isTemporalColumn,
  numericValue,
} from "./columns";

export const CHART_TYPES = [
  { value: "bar", label: "柱状" },
  { value: "line", label: "折线" },
  { value: "pie", label: "饼图" },
];

const MAX_ROWS = 50;
const MAX_SERIES = 6;
const MAX_MEASURES = 3;

export const PALETTE = [
  "#2f6fed", "#16a37f", "#e8a33d", "#8a63e8",
  "#e06666", "#3aa8c1", "#8ab03c", "#d4739e",
];

function byKey(columns, keys) {
  if (!Array.isArray(keys) || !keys.length) return [];
  return keys.map((key) => columns.find(( column) => column.key === key)).filter(Boolean);
}

/** tooltip 按 HTML 渲染，分组值来自业务数据，必须转义后再拼 */
function escapeHtml(text) {
  return String(text)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/**
 * 从表格推断图表模型；不可画时返回 null。
 * @param {object} table {columns, rows, chart?}
 * @returns {null | {
 *   dimension:{key,title}, split:null|{key,title}, measures:Array<{key,title}>,
 *   categories:string[], series:Array<{name:string,data:number[]}>,
 *   types:string[], defaultType:string, truncated:boolean, note:string
 * }}
 */
export function buildChartModel(table) {
  const columns = (table?.columns || []).filter((c) => c && c.key);
  const rows = (table?.rows || []).filter((r) => r && typeof r === "object");
  if (columns.length < 2 || rows.length < 2) return null;

  const hint = table.chart || {};
  const dimensions = byKey(columns, hint.dimensions);
  const hinted = byKey(columns, hint.measures);

  const allDimensions = dimensions.length
    ? dimensions
    : columns.filter((c) => !isNumericColumn(c));
  const allMeasures = (hinted.length
    ? hinted
    : columns.filter((c) => isNumericColumn(c) && !isIdColumn(c))).slice(0, MAX_MEASURES);

  if (!allDimensions.length || !allMeasures.length) return null;

  const dimension = pickDimension(allDimensions, rows);
  const split = pickSplit(allDimensions, dimension, rows);
  const trimmed = rows.slice(0, MAX_ROWS);
  const truncated = rows.length > trimmed.length;

  const { categories, series } = split
    ? pivot(trimmed, dimension, split, allMeasures)
    : straight(trimmed, dimension, allMeasures);

  if (categories.length < 2) return null;

  const pieOk = allMeasures.length === 1 && !split && categories.length <= 12;
  const types = pieOk ? ["bar", "line", "pie"] : ["bar", "line"];
  const defaultType = isTemporalColumn(dimension) ? "line" : "bar";

  const notes = [];
  if (split) notes.push(`按「${dimension.title}」分组，系列取「${split.title}」前 ${MAX_SERIES} 类`);
  if (truncated) notes.push(`图表仅用前 ${MAX_ROWS} 行，表格看全量`);

  return {
    dimension: { key: dimension.key, title: dimension.title || dimension.key },
    split: split ? { key: split.key, title: split.title || split.key } : null,
    measures: allMeasures.map((c) => ({ key: c.key, title: c.title || c.key })),
    categories,
    series,
    types,
    defaultType,
    truncated,
    note: notes.join(" · "),
  };
}

/**
 * 选 X 轴。
 *
 * 时间列不是天然就是轴的：明细表里每行一个时间点（创建时间、开始时间）时，
 * 它只是一条普通属性——拿它当轴会得到「每个活动一个系列、各只有一根针」的
 * 乱图。只有多行落在同一时间点上（真正的时间序列）才让时间当轴。
 */
function pickDimension(dimensions, rows) {
  const temporal = dimensions.find(isTemporalColumn);
  const categorical = dimensions.find((c) => !isIdColumn(c) && !isTemporalColumn(c))
    || dimensions.find((c) => !isIdColumn(c));

  if (!temporal) return categorical || dimensions[0];
  if (distinctCount(rows, temporal.key) < rows.length) return temporal;   // 时间有重复 → 时间序列
  return categorical || temporal;
}

/**
 * 选系列（第二个维度的透视）。
 *
 * 只在「确实是个交叉表」时才拆系列：行数要多于主维度类别数，否则每个系列
 * 只有一根柱子，图例比图还长。拆分列的多值上限也跟着 MAX_SERIES 走。
 */
function pickSplit(dimensions, dimension, rows) {
  const candidate = dimensions.find((c) => c.key !== dimension.key);
  if (!candidate) return null;
  const seriesCount = distinctCount(rows, candidate.key);
  const categories = distinctCount(rows, dimension.key);
  if (seriesCount > MAX_SERIES) return null;
  return rows.length > categories ? candidate : null;
}

function distinctCount(rows, key) {
  const seen = new Set();
  for (const row of rows) seen.add(String(row?.[key]));
  return seen.size;
}

function straight(rows, dimension, measures) {
  const categories = rows.map((row) => dimensionLabel(row[dimension.key]));
  const series = measures.map((column) => ({
    name: column.title || column.key,
    data: rows.map((row) => numericValue(row[column.key]) ?? 0),
  }));
  return { categories, series };
}

/** 两个维度：第一维做 x 轴，第二维透视成多条系列（最多 6 条，其余忽略） */
function pivot(rows, dimension, split, measures) {
  const measure = measures[0];
  const categories = [];
  const seen = new Set();
  const names = [];
  const nameSet = new Set();

  for (const row of rows) {
    const label = dimensionLabel(row[dimension.key]);
    if (!seen.has(label)) { seen.add(label); categories.push(label); }
    const name = dimensionLabel(row[split.key]);
    if (!nameSet.has(name) && names.length < MAX_SERIES) { nameSet.add(name); names.push(name); }
  }

  const bucket = new Map();
  for (const row of rows) {
    const label = dimensionLabel(row[dimension.key]);
    const name = dimensionLabel(row[split.key]);
    if (!nameSet.has(name)) continue;
    const key = label + "\u0000" + name;
    bucket.set(key, (bucket.get(key) || 0) + (numericValue(row[measure.key]) ?? 0));
  }

  const series = names.map((name) => ({
    name,
    data: categories.map((label) => bucket.get(label + "\u0000" + name) || 0),
  }));
  return { categories, series };
}

/**
 * 生成 ECharts option。
 * @param {object} model buildChartModel 的结果
 * @param {"bar"|"line"|"pie"} type
 * @param {object} theme {ink, ink2, ink3, line, surface}
 */
export function buildChartOption(model, type, theme) {
  if (!model) return null;
  const base = {
    color: PALETTE,
    animationDuration: 420,
    textStyle: { fontFamily: "inherit" },
  };

  if (type === "pie") {
    return {
      ...base,
      tooltip: {
        trigger: "item",
        backgroundColor: theme.surface,
        borderColor: theme.line,
        textStyle: { color: theme.ink, fontSize: 12 },
        formatter: (item) => `${escapeHtml(item.name)}<br/><b>${formatNumber(item.value)}</b> · ${item.percent}%`,
      },
      legend: {
        type: "scroll",
        bottom: 0,
        icon: "circle",
        itemWidth: 8,
        itemHeight: 8,
        textStyle: { color: theme.ink2, fontSize: 11 },
      },
      series: [{
        type: "pie",
        radius: ["40%", "68%"],
        center: ["50%", "45%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: theme.surface, borderWidth: 2 },
        label: {
          color: theme.ink2,
          fontSize: 11,
          formatter: "{b}\n{d}%",
          lineHeight: 15,
        },
        labelLine: { length: 8, length2: 6, lineStyle: { color: theme.line } },
        data: model.categories.map((name, index) => ({
          name,
          value: model.series[0]?.data[index] ?? 0,
        })),
      }],
    };
  }

  const rotate = model.categories.length > 7 ? 32 : 0;
  const isBar = type === "bar";
  return {
    ...base,
    grid: {
      left: 6,
      right: 14,
      top: model.series.length > 1 ? 34 : 14,
      bottom: rotate ? 22 : 4,
      containLabel: true,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: isBar ? "shadow" : "line", lineStyle: { color: theme.line } },
      backgroundColor: theme.surface,
      borderColor: theme.line,
      textStyle: { color: theme.ink, fontSize: 12 },
      valueFormatter: (value) => formatNumber(value, Number.isInteger(value) ? 0 : 2),
    },
    legend: model.series.length > 1
      ? {
        top: 0,
        right: 0,
        icon: "roundRect",
        itemWidth: 10,
        itemHeight: 10,
        textStyle: { color: theme.ink2, fontSize: 11 },
      }
      : undefined,
    xAxis: {
      type: "category",
      data: model.categories,
      axisLine: { lineStyle: { color: theme.line } },
      axisTick: { show: false },
      axisLabel: {
        color: theme.ink3,
        fontSize: 11,
        interval: 0,
        rotate,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: theme.line, type: "dashed" } },
      axisLabel: { color: theme.ink3, fontSize: 11, formatter: (value) => compactNumber(value) },
    },
    series: model.series.map((item) => (isBar
      ? {
        name: item.name,
        type: "bar",
        data: item.data,
        barMaxWidth: 34,
        itemStyle: { borderRadius: [4, 4, 0, 0] },
        emphasis: { focus: "series" },
      }
      : {
        name: item.name,
        type: "line",
        data: item.data,
        smooth: true,
        symbol: "circle",
        symbolSize: 6,
        lineStyle: { width: 2 },
        areaStyle: model.series.length === 1
          ? { opacity: 0.12 }
          : undefined,
        emphasis: { focus: "series" },
      })),
  };
}
