/**
 * 路由切换后把焦点交给主内容（批 112）。
 *
 * ## 为什么要做
 *
 * 实机实测：在 `/admin/overview` 点侧栏链接跳到 `/admin/security` 后，
 * `document.activeElement` 是 **`BODY`** —— 标题虽然变了（各页 title 都正确），
 * 但**焦点没人管**。对键盘/读屏用户来说，切页之后没有任何"我到新页面了"的信号，
 * 下一次 Tab 只能从文档最前面重新摸一遍。这是 SPA 常见的漏项。
 *
 * 本仓已经具备接上的全部条件：两个布局的主内容都是
 * `<main id="main-content" tabindex="-1">`（跳转链接也正是指向它），
 * 所以这里只补"切页之后把焦点放过去"这一步。
 *
 * ## 关键：**首次进入不抢焦点**
 *
 * 只在 `route.fullPath` **变化**时动作（`watch` 不带 `immediate`），
 * 首次渲染不动手。否则一进站焦点就在 `main` 里，
 * 键盘用户按 Tab 会直接落进主内容 —— **「跳到主内容」就永远用不上了**。
 * 这条与批 111 修的正数 tabindex 是同一个主题的两面：**别抢旁路机制的位置**。
 *
 * 用 `fullPath` 而不是 `path`：同一路径换 query（例如筛选写进 URL）也算切页。
 */
import { nextTick, watch } from 'vue';
import { useRoute } from 'vue-router';

export function useRouteFocus(containerId = 'main-content') {
  const route = useRoute();
  watch(
    () => route.fullPath,
    () => {
      nextTick(() => {
        const el = document.getElementById(containerId);
        // 不 preventScroll：切页本来就该回到内容顶部（路由 scrollBehavior 也是 top:0）
        el?.focus();
      });
    },
  );
}
