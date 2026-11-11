<script setup>
import { computed } from "vue";
import { formatCell, isNumericColumn } from "../lib/columns";

const props = defineProps({
  table: { type: Object, required: true },
});

const LIMIT = 100;

const columns = computed(() => props.table.columns || []);

/**
 * 一次算好整张表的展示值。
 * 在模板里对每个单元格调三次 formatCell（文本 / 色调 / title）会让 100×8 的表
 * 变成两千多次格式化——这里按行摊平，模板只做取值。
 */
const lines = computed(() => (props.table.rows || []).slice(0, LIMIT).map((row) => (
  columns.value.map((column) => {
    const cell = formatCell(row[column.key], column);
    return {
      key: column.key,
      text: cell.text,
      tone: cell.tone,
      title: cell.title,
      numeric: isNumericColumn(column),
    };
  })
)));
</script>

<template>
  <div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th
            v-for="column in columns"
            :key="column.key"
            :style="{ textAlign: isNumericColumn(column) ? 'right' : 'left' }"
          >
            {{ column.title || column.key }}
          </th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(line, index) in lines" :key="index">
          <td
            v-for="cell in line"
            :key="cell.key"
            :class="{ num: cell.numeric, muted: cell.tone === 'muted' }"
            :style="{ textAlign: cell.numeric ? 'right' : 'left' }"
          >
            <span :title="cell.title">{{ cell.text }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.table-scroll {
  overflow: auto;
  max-height: 420px;
  scrollbar-color: var(--line-strong) transparent;
}
.table-scroll::-webkit-scrollbar { height: 10px; }
.table-scroll::-webkit-scrollbar-thumb {
  background: var(--line-strong);
  border: 2px solid var(--surface);
  border-radius: 999px;
}

table { border-collapse: separate; border-spacing: 0; width: 100%; font-size: calc(12.8px * var(--scale)); }
th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--surface-2);
  color: var(--ink-2);
  font-weight: 600;
  text-align: left;
  padding: 8px 13px;
  white-space: nowrap;
  border-bottom: 1px solid var(--line);
}
td {
  padding: 8px 13px;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
  max-width: 320px;
  overflow: hidden;
  text-overflow: ellipsis;
}
td.num { font-variant-numeric: tabular-nums; }
td.muted span { color: var(--ink-3); }
tbody tr:last-child td { border-bottom: 0; }
tbody tr:hover td { background: var(--bg-soft); }
</style>
