import { useState } from 'react'
import spiderAssetUrl from '../../assets/spider-cutout.png'

export { spiderAssetUrl }

export function SpiderHub() {
  const [assetMissing, setAssetMissing] = useState(false)
  return <div className="spider-hub" aria-hidden="true">
    <div className="spider-halo" />
    {!assetMissing ? <img src={spiderAssetUrl} alt="" draggable={false} onError={() => setAssetMissing(true)} /> : <span className="spider-asset-missing">Spider asset unavailable</span>}
    <span className="spider-core-glow" />
  </div>
}
