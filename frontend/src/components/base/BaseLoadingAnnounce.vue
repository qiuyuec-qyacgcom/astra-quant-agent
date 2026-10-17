<script setup lang="ts">
/**
 * 骨架屏的**读屏播报**（批 113）。
 *
 * ## 为什么要它
 *
 * 全站 18 个文件渲染 `.skeleton` 骨架，但只有 `DataGate` 做了播报
 * （它有一条**可见**的"正在加载"文案 + `aria-busy="true"`，实测唯一一个）。
 * 其余 17 个管理页在首屏加载时只画骨架 —— 而骨架是**空 div**，
 * 读屏器读到的就是"页头之后什么都没有"：用户既不知道在加载，
 * 也不知道内容什么时候到位。`RefreshCw`/`Loader2` 转圈只是视觉的。
 *
 * ## 为什么是独立组件而不是各页各写一行
 *
 * 骨架的容器大多是只带 `v-if` 的 template 标签，而 template 标签**挂不了 ARIA 属性**；
 * 各页自己写就要各自处理 i18n 与 `sr-only`，漏一个就少一页播报。
 * 收敛成一个组件后，页面只需插一个标签，文案与角色只有一处定义。
 *
 * ⚠️ 本文件的注释里**故意不出现字面量的 template 开标签或收尾标签**。
 * 本仓约 20 个判据用同一种贪婪正则取 SFC 模板块（起点 = 文件里第一个
 * 不含属性的 template 开标签，终点 = 最后一个收尾标签）。脚本区里只要出现一个
 * 裸开标签，匹配就会**从注释开始**，判据从此看的是注释文字 ——
 * **静默看错地方、不报错**。批 113 实测：本组件第一版注释正是这么写的，
 * `hardcodedChineseUiText` 当场把注释里的中文当成"模板硬编码中文"报了出来；
 * 而 `tests/sfcTemplateExtraction.test.mjs` 现在全仓守住这条（它写完当场又抓到
 * 我解释这个坑时顺手写下的裸标签 —— 所以这段文字只能用自然语言描述）。
 *
 * ## 为什么不会影响布局
 *
 * `.sr-only`（Tailwind 核心工具类）是 `position:absolute` + 1×1 + 裁剪，
 * **脱离文档流**：插在 flex/grid 容器里也不会成为 flex item / grid item。
 * 实机验证过插入前后页面几何完全一致（见批 113 报告）。
 *
 * ## 与 DataGate 的分工
 *
 * `DataGate` 已有可见文案 + `aria-busy`，**不要再插本组件**，否则同一页播报两遍。
 */
import { useI18n } from '../../composables/useI18n';

const { t } = useI18n();
</script>

<template>
  <span class="sr-only" role="status">{{ t('common.loading') }}</span>
</template>
