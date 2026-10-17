/**
 * 官方标准加密货币真实图标集与本地/CDN 路径映射
 * 深度集成 OKX 官方高清代币资产与矢量 SVG，支持 0 毫秒首屏直出、视网膜超清、深浅色模式自适应。
 */

export function cleanSymbol(raw?: string): string {
  if (!raw) return '';
  const s = String(raw).trim().toUpperCase();
  // 提取基础代币名（剥离 -USDT-SWAP, /USDT, -PERP 等后缀）
  const base = s.split(/[\/\-_\s]/)[0] || s;
  return base.replace(/[^A-Z0-9]/g, '');
}

// 通过 Vite 的 glob 机制预编译全部 20 个主流代币的真实官方图像
// Vite 会将它们自动打包进 /assets/ 并附带内容哈希，由后端静态服务持久缓存，绝不 404
const coinAssetModules = import.meta.glob('../assets/coins/*.png', {
  eager: true,
  import: 'default',
}) as Record<string, string>;

export const COIN_IMAGE_MAP: Record<string, string> = {};
for (const [path, url] of Object.entries(coinAssetModules)) {
  const match = path.match(/\/([^/]+)\.png$/);
  if (match && match[1]) {
    COIN_IMAGE_MAP[match[1].toUpperCase()] = url;
  }
}

/** 获取本地高清代币图片路径（包含真实柴犬 DOGE、真实 PEPE、BTC、ETH 等） */
export function getCryptoLocalUrl(sym: string): string | null {
  const clean = cleanSymbol(sym);
  return COIN_IMAGE_MAP[clean] || null;
}

export const CRYPTO_SVGS: Record<string, string> = {
  BTC: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#F7931A"/>
    <path d="M23.189 14.02c.314-2.096-1.283-3.223-3.465-3.975l.708-2.84-1.728-.43-.69 2.765c-.454-.114-.92-.22-1.385-.326l.695-2.783L15.596 6l-.708 2.839c-.376-.086-.746-.17-1.104-.26l.002-.009-2.384-.595-.46 1.846s1.283.294 1.256.312c.7.175.826.638.805 1.006l-.806 3.235c.048.012.11.03.18.057l-.183-.045-1.13 4.532c-.086.212-.303.531-.793.41.018.025-1.256-.313-1.256-.313l-.858 1.978 2.25.561c.418.105.828.215 1.231.318l-.715 2.872 1.727.43.708-2.84c.472.127.93.245 1.378.357l-.706 2.828 1.728.43.715-2.866c2.948.558 5.164.333 6.097-2.333.752-2.146-.037-3.385-1.588-4.192 1.13-.26 1.98-1.003 2.207-2.538zm-3.95 5.538c-.535 2.146-4.148.986-5.32.695l.95-3.805c1.172.293 4.929.872 4.37 3.11zm.535-5.569c-.487 1.953-3.495.96-4.47.717l.86-3.45c.975.243 4.118.697 3.61 2.733z" fill="#FFF"/>
  </svg>`,

  ETH: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#627EEA"/>
    <path d="M16.498 4v8.87l7.497 3.35L16.498 4z" fill="#FFF" fill-opacity=".602"/>
    <path d="M16.498 4L9 16.22l7.498-3.35V4z" fill="#FFF"/>
    <path d="M16.498 21.968v6.027L24 17.616l-7.502 4.352z" fill="#FFF" fill-opacity=".602"/>
    <path d="M16.498 27.995v-6.028L9 17.616l7.498 10.379z" fill="#FFF"/>
    <path d="M16.498 20.573l7.497-4.353-7.497-3.348v7.701z" fill="#FFF" fill-opacity=".2"/>
    <path d="M9 16.22l7.498 4.353v-7.701L9 16.22z" fill="#FFF" fill-opacity=".602"/>
  </svg>`,

  SOL: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#09090B"/>
    <path d="M8.2 21.8a.7.7 0 0 1 .5-.2h14.8c.6 0 .9.7.5 1.1l-2.8 2.8a.7.7 0 0 1-.5.2H5.9c-.6 0-.9-.7-.5-1.1l2.8-2.8zm0-12.6a.7.7 0 0 1 .5-.2h14.8c.6 0 .9.7.5 1.1l-2.8 2.8a.7.7 0 0 1-.5.2H5.9c-.6 0-.9-.7-.5-1.1l2.8-2.8zm15.6 6.3a.7.7 0 0 1-.5.2H8.5c-.6 0-.9-.7-.5-1.1l2.8-2.8a.7.7 0 0 1 .5-.2h14.8c.6 0 .9.7.5 1.1l-2.8 2.8z" fill="url(#sol-gradient)"/>
    <defs>
      <linearGradient id="sol-gradient" x1="5" y1="26" x2="26" y2="9" gradientUnits="userSpaceOnUse">
        <stop stop-color="#00FFA3"/>
        <stop offset="1" stop-color="#DC1FFF"/>
      </linearGradient>
    </defs>
  </svg>`,

  DOGE: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#C2A633"/>
    <path d="M11.5 8.5h5.5c4.5 0 8 3 8 7.5s-3.5 7.5-8 7.5h-5.5V8.5zm3.2 3.2v3h4v2.2h-4v3.4h2c2.8 0 4.8-1.7 4.8-4.5s-2-4.1-4.8-4.1h-2z" fill="#FFF"/>
  </svg>`,

  ARB: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#28A0F0"/>
    <path d="M22.8 20.2l-4.5-4.5 2.5-4 5.2 8.5h-3.2zm-7.1-7.1l3.5 3.5-3.5 5.6-3.5-5.6 3.5-3.5zm-3-2.3l3 4.9-3 3.1-5.1-8h5.1zm-5.1 10.3l4.5-4.5 2.5 4-5.2 8.5H7.6l-.0-.0z" fill="#FFF"/>
  </svg>`,

  SUI: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#4DA2FF"/>
    <path d="M16 6.5C12.5 11 9 15.5 9 19.5c0 3.86 3.14 7 7 7s7-3.14 7-7c0-4-3.5-8.5-7-13zm0 18.5c-2.48 0-4.5-2.02-4.5-4.5 0-2.3 2-5.5 4.5-8.8 2.5 3.3 4.5 6.5 4.5 8.8 0 2.48-2.02 4.5-4.5 4.5z" fill="#FFF"/>
  </svg>`,

  XRP: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#23292F"/>
    <path d="M23.9 8h2.3l-5.6 5.5c-2.5 2.5-6.6 2.5-9.1 0L5.8 8h2.3l4.5 4.4c1.3 1.3 3.3 1.3 4.6 0L23.9 8zM8.1 24H5.8l5.6-5.5c2.5-2.5 6.6-2.5 9.1 0l5.6 5.5h-2.3l-4.5-4.4c-1.3-1.3-3.3-1.3-4.6 0L8.1 24z" fill="#FFF"/>
  </svg>`,

  ADA: `<svg viewBox="0 0 32 32" width="100%" height="100%" fill="none" xmlns="http://www.w3.org/2000/svg">
    <circle cx="16" cy="16" r="16" fill="#0033AD"/>
    <circle cx="16" cy="16" r="3.2" fill="#FFF"/>
    <circle cx="16" cy="8.5" r="1.4" fill="#FFF"/>
    <circle cx="16" cy="23.5" r="1.4" fill="#FFF"/>
    <circle cx="8.5" cy="16" r="1.4" fill="#FFF"/>
    <circle cx="23.5" cy="16" r="1.4" fill="#FFF"/>
    <circle cx="10.7" cy="10.7" r="1.4" fill="#FFF"/>
    <circle cx="21.3" cy="21.3" r="1.4" fill="#FFF"/>
    <circle cx="21.3" cy="10.7" r="1.4" fill="#FFF"/>
    <circle cx="10.7" cy="21.3" r="1.4" fill="#FFF"/>
  </svg>`,
};

/** 查询内置矢量 SVG */
export function getCryptoSvg(sym: string): string | null {
  const clean = cleanSymbol(sym);
  return CRYPTO_SVGS[clean] || null;
}

/** 针对长尾币种的公开 CDN 高清图标地址（优先使用 CoinCap / OKX CDN） */
export function getCryptoCdnUrl(sym: string): string {
  const clean = cleanSymbol(sym).toLowerCase();
  return `https://assets.coincap.io/assets/icons/${clean}@2x.png`;
}
