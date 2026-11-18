<script setup>
defineProps({
  name: { type: String, required: true },
  size: { type: [Number, String], default: null },
});
</script>

<template>
  <svg
    class="i"
    aria-hidden="true"
    :style="size ? { width: size + 'px', height: size + 'px' } : null"
  >
    <use :href="'#i-' + name" />
  </svg>
</template>
