<script setup>
/** 原生 <dialog> 的薄封装：保留 Esc / 焦点陷阱 / 顶层渲染，不做自绘遮罩。 */
import { onMounted, ref, watch } from "vue";

const props = defineProps({
  open: { type: Boolean, default: false },
  size: { type: String, default: "md" },   // md | sm
});
const emit = defineEmits(["close"]);

const el = ref(null);

function sync(open) {
  const dialog = el.value;
  if (!dialog) return;
  if (open && !dialog.open) dialog.showModal();
  if (!open && dialog.open) dialog.close();
}

watch(() => props.open, sync);
onMounted(() => sync(props.open));

function onCancel(event) {
  event.preventDefault();
  emit("close");
}

function onClick(event) {
  if (event.target === el.value) emit("close");
}
</script>

<template>
  <dialog
    ref="el"
    class="modal"
    :class="{ 'modal-sm': size === 'sm' }"
    @cancel="onCancel"
    @close="emit('close')"
    @click="onClick"
  >
    <div class="modal-card">
      <slot />
    </div>
  </dialog>
</template>
