<template>
  <div class="ac-title" :class="`ac-title--${color}`" :style="{ fontSize: sizeMap[size] }">
    <span class="ac-title__tail ac-title__tail--left"></span>
    <span class="ac-title__tail ac-title__tail--right"></span>
    <span class="ac-title__fold ac-title__fold--left"></span>
    <span class="ac-title__fold ac-title__fold--right"></span>
    <span class="ac-title__front"></span>
    <span class="ac-title__text"><slot /></span>
  </div>
</template>

<script setup>
defineProps({
  size: { type: String, default: 'middle' }, // small / middle / large
  color: { type: String, default: 'teal' },  // teal / green / yellow / blue / pink
})

const sizeMap = {
  small: '14px',
  middle: '20px',
  large: '28px',
}
</script>

<style scoped>
/* 缎带标题：燕尾 + 3D 折叠（em-based，随 font-size 缩放）
   参考 animal-island-ui Title 组件规范 */
.ac-title {
  /* 配色变量（各色板在下方定义） */
  --rf: #19c8b9;
  --rb: #11a89b;
  --rk: #0a6b62;
  --rt: #fff;

  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 2em;
  padding: 0 1.6em;
  filter: drop-shadow(0 0.08em 0.12em rgba(0, 0, 0, 0.05));
  /* 折叠三角与燕尾悬在盒外，需要预留空间 */
  margin: 0 1em 0.5em;
  vertical-align: middle;
}

/* 前面板 */
.ac-title__front {
  position: absolute;
  inset: 0 0.1em;
  background: var(--rf);
  border-radius: 0.2em;
  transform: perspective(11.5em) rotateX(3deg);
  box-shadow: inset 0 -0.06em 0 rgba(0, 0, 0, 0.05);
  z-index: 2;
}

/* 后部燕尾 */
.ac-title__tail {
  position: absolute;
  width: 1.7em;
  height: 1.7em;
  background: var(--rb);
  bottom: -0.4em;
  z-index: 1;
}
.ac-title__tail--left {
  left: -0.75em;
  clip-path: polygon(100% 0%, 100% 100%, 0% 100%, 30% 50%, 0% 0%);
}
.ac-title__tail--right {
  right: -0.75em;
  clip-path: polygon(0% 0%, 100% 0%, 70% 50%, 100% 100%, 0% 100%);
}

/* 折叠三角（3D 阴影） */
.ac-title__fold {
  position: absolute;
  width: 0;
  height: 0;
  border-style: solid;
  top: calc(100% - 0.04em);
  z-index: 1;
}
.ac-title__fold--left {
  left: 0.1em;
  border-width: 0 0.95em 0.45em 0;
  border-color: transparent var(--rk) transparent transparent;
}
.ac-title__fold--right {
  right: 0.1em;
  border-width: 0 0 0.45em 0.95em;
  border-color: transparent transparent transparent var(--rk);
}

/* 文字 */
.ac-title__text {
  position: relative;
  z-index: 3;
  color: var(--rt);
  font-weight: 900;
  line-height: 1;
  letter-spacing: 0.01em;
  padding-top: 0.11em; /* CJK 视觉居中 */
  white-space: nowrap;
}

/* ---- 色板 ---- */
.ac-title--teal {
  --rf: #19c8b9; --rb: #11a89b; --rk: #0a6b62; --rt: #fff;
}
.ac-title--green {
  --rf: #27d039; --rb: #20992a; --rk: #115017; --rt: #fff;
}
.ac-title--yellow {
  --rf: #f7cd67; --rb: #d4a030; --rk: #8a6010; --rt: #725d42;
}
.ac-title--blue {
  --rf: #889df0; --rb: #5f74d4; --rk: #35479e; --rt: #fff;
}
.ac-title--pink {
  --rf: #f8a6b2; --rb: #e07b8c; --rk: #a84557; --rt: #fff;
}
</style>
