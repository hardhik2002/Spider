import { useState } from 'react'

const spiderAssets = import.meta.glob<string>('../../assets/spider-reference.png', {
  eager: true, query: '?url', import: 'default',
})
export const spiderAssetUrl = Object.values(spiderAssets)[0]

export function SpiderHub() {
  const [assetMissing, setAssetMissing] = useState(false)
  return <div className="spider-hub" aria-hidden="true">
    <div className="spider-halo" />
    {spiderAssetUrl && !assetMissing ? <img src={spiderAssetUrl} alt="" draggable={false} onError={() => setAssetMissing(true)} /> : <span className="spider-asset-missing">Spider asset pending</span>}
    <span className="spider-core-glow" />
  </div>
}
