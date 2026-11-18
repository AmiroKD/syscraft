<script setup>
import { computed } from "vue";
import BaseDialog from "./BaseDialog.vue";
import { useUiStore } from "../stores/ui";

const ui = useUiStore();
const state = computed(() => ui.confirm);
</script>

<template>
  <BaseDialog :open="state.open" size="sm" @close="ui.answerConfirm(false)">
    <div class="modal-head">
      <div><h2>{{ state.title }}</h2></div>
    </div>
    <div class="modal-body">
      <p class="text">{{ state.text }}</p>
    </div>
    <div class="modal-foot">
      <button class="btn-ghost" type="button" @click="ui.answerConfirm(false)">取消</button>
      <button class="btn-danger" type="button" @click="ui.answerConfirm(true)">{{ state.okText }}</button>
    </div>
  </BaseDialog>
</template>

<style scoped>
.text { margin: 12px 0 4px; font-size: 13px; line-height: 1.75; color: var(--ink-2); }
</style>
