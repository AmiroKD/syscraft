/**
 * 列与值：类型判定 + 展示格式化。
 *
 * 类型直接来自产物侧的 SQL 类型（VARCHAR / INT / DECIMAL(10,2) / DATETIME …），
 * 前端只按「数字 / 时间 / 其他」三类分流，不做语义猜测。
 *
 * 不做时区转换：后端统一返回本地时间字符串，前端再转一次只会引入第二次错误。
 */

const NUMERIC = new Set([
  "TINYINT", "SMALLINT", "MEDIUMINT", "INT", "INTEGER", "BIGINT",
  "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "NUMBER",
]);
const TEMPORAL = new Set(["DATE", "DATETIME", "TIMESTAMP", "TIME", "YEAR"]);

export function baseType(type) {
  return String(type || "").split("(")[0].trim().toUpperCase();
}

export function typeScale(type) {
  const match = String(type || "").match(/\(\s*\d+\s*,\s*(\d+)\s*\)/);
  return match ? Number(match[1]) : null;
}

export function isNumericColumn(column) {
  return NUMERIC.has(baseType(column?.type));
}

export function isTemporalColumn(column) {
  return TEMPORAL.has(baseType(column?.type));
}

export function isDecimalColumn(column) {
  const type = baseType(column?.type);
  return type === "DECIMAL" || type === "NUMERIC" || type === "FLOAT" || type === "DOUBLE" || type === "REAL";
}

/** 主键 / 外键列：画图时不该当度量，表格里也没必要抢宽度 */
export function isIdColumn(column) {
  const key = String(column?.key || "").toLowerCase();
  return key === "id" || key.endsWith("_id") || key === "uuid" || key === "pk";
}

const numberFormatCache = new Map();

function numberFormat(decimals) {
  const key = decimals ?? "auto";
  if (!numberFormatCache.has(key)) {
    numberFormatCache.set(key, new Intl.NumberFormat("zh-CN", {
      minimumFractionDigits: decimals ?? 0,
      maximumFractionDigits: decimals ?? 3,
    }));
  }
  return numberFormatCache.get(key);
}

export function formatNumber(value, decimals) {
  const num = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(num)) return String(value ?? "");
  const precision = decimals == null ? (Number.isInteger(num) ? 0 : 2) : decimals;
  return numberFormat(precision).format(num);
}

/** 图表轴上的紧凑数字：12000 → 1.2万 */
export function compactNumber(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value ?? "");
  const abs = Math.abs(num);
  if (abs >= 100_000_000) return (num / 100_000_000).toFixed(1).replace(/\.0$/, "") + "亿";
  if (abs >= 10_000) return (num / 10_000).toFixed(1).replace(/\.0$/, "") + "万";
  return formatNumber(num, Number.isInteger(num) ? 0 : 2);
}

function pad(value) {
  return String(value).padStart(2, "0");
}

/** 时间值统一成 `YYYY-MM-DD` / `YYYY-MM-DD HH:mm`；解析不了就原样返回 */
export function formatTemporal(value, type) {
  if (value == null) return "";
  const base = baseType(type);
  if (typeof value === "number") {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const datePart = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    return base === "DATE" ? datePart : `${datePart} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  const raw = String(value).trim();
  if (!raw) return "";
  const normalized = raw.includes("T") ? raw.replace("T", " ") : raw;
  if (base === "DATE") return normalized.slice(0, 10);
  if (base === "TIME") return normalized.slice(-8, -3) || normalized;
  return normalized.slice(0, 16);
}

/**
 * 单元格展示值。
 * @returns {{text:string, tone:'normal'|'muted'|'number'|'date', title:string}}
 */
export function formatCell(value, column) {
  if (value === null || value === undefined || value === "") {
    return { text: "—", tone: "muted", title: "空值" };
  }
  if (typeof value === "boolean") {
    return { text: value ? "是" : "否", tone: "normal", title: String(value) };
  }
  if (isNumericColumn(column)) {
    const decimals = isDecimalColumn(column) ? typeScale(column.type) ?? 2 : 0;
    const text = formatNumber(value, decimals);
    return { text, tone: "number", title: text };
  }
  if (isTemporalColumn(column)) {
    const text = formatTemporal(value, column.type);
    return { text, tone: "date", title: String(value) };
  }
  if (typeof value === "object") {
    const text = JSON.stringify(value);
    return { text, tone: "normal", title: text };
  }
  const text = String(value);
  return { text, tone: "normal", title: text };
}

/** 单元格的原始数字值（画图用）：非数字返回 null */
export function numericValue(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "boolean") return value ? 1 : 0;
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

/** 维度值转成轴上的一行文字 */
export function dimensionLabel(value) {
  if (value === null || value === undefined || value === "") return "（空）";
  if (typeof value === "boolean") return value ? "是" : "否";
  const text = String(value);
  return text.length > 18 ? text.slice(0, 17) + "…" : text;
}
