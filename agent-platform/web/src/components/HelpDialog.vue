<script setup>
import { computed } from "vue";
import AppIcon from "./AppIcon.vue";
import BaseDialog from "./BaseDialog.vue";
import { useUiStore } from "../stores/ui";

const ui = useUiStore();
const open = computed(() => ui.dialog === "help");

const items = [
  { title: "查数据", text: "直接说「查一下活动有多少条」「名额上限大于 50 的活动」。" },
  { title: "做统计", text: "「按报名状态统计」「每个活动的报名人数」——结果会同时给出表格和图表。" },
  { title: "写数据", text: "新增、修改直接描述；删除会先生成预告，回复「确认」才执行。" },
  { title: "造系统", text: "「做一个图书借阅管理系统」，确认计划后约 2 分钟上线。" },
  { title: "改结构", text: "「给活动加一个备注字段」，确认后自动迁移并重新上线。" },
  { title: "换模型", text: "设置 → 模型服务，填写 Base URL、API Key 和模型名，可先测试连接再保存。" },
];

const shortcuts = [
  { key: "新对话", value: "⌘ / Ctrl + K" },
  { key: "发送消息", value: "Enter" },
  { key: "换行", value: "Shift + Enter" },
  { key: "关闭弹层", value: "Esc" },
];
</script>

<template>
  <BaseDialog :open="open" @close="ui.closeDialog()">
    <div class="modal-head">
      <div>
        <h2>帮助</h2>
        <p>几句最短的上手说明</p>
      </div>
      <button class="icon-btn" type="button" aria-label="关闭" @click="ui.closeDialog()">
        <AppIcon name="x" />
      </button>
    </div>
    <div class="modal-body">
      <section class="setting-group">
        <h3>可以做什么</h3>
        <ul class="help-list">
          <li v-for="item in items" :key="item.title">
            <strong>{{ item.title }}</strong>：{{ item.text }}
          </li>
        </ul>
      </section>
      <section class="setting-group">
        <h3>快捷键</h3>
        <div class="kv-list">
          <div v-for="item in shortcuts" :key="item.key">
            <span>{{ item.key }}</span><b>{{ item.value }}</b>
          </div>
        </div>
      </section>
      <p class="muted">
        会话保存在平台自己的 chat 库，刷新或换设备登录后仍可翻看；生成期间产物会短暂停机，查询需要等生成结束。
      </p>
    </div>
    <div class="modal-foot">
      <button class="btn-primary" type="button" @click="ui.closeDialog()">知道了</button>
    </div>
  </BaseDialog>
</template>

<style scoped>
.help-list { margin: 8px 0 6px; padding-left: 20px; font-size: 12.5px; line-height: 1.85; color: var(--ink-2); }
.help-list li::marker { color: var(--ink-3); }
.help-list strong { color: var(--ink); font-weight: 600; }
</style>
