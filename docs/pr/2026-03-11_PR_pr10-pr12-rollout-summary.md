# PR10 / PR11 / PR12 Rollout Summary

## 建議先後順序
1. **PR10 — Shard / Persistence 抽層**
2. **PR11 — State / Store 分層 + bootstrap 清晰化**
3. **PR12 — Search orchestration 收斂**

---

## 穩定度與風險對比

- **最穩（最低風險）**：PR10
  - 原因：邊界明確、可做純內部搬遷、對外 API 幾乎不動。

- **中風險**：PR11
  - 原因：會碰到 state 管理與 bootstrap，需處理 `_translation_cache/_initialized` 相容。

- **風險最高**：PR12
  - 原因：search lifecycle + metadata + UI contract 互相牽動，若處理不慎會有搜尋結果回歸問題。

---

## 最適合立刻開工
**PR10**。

理由：
- 可在小範圍驗證 persistence 行為；
- 對 PR11/PR12 都是降耦合前置；
- 最容易做到「可回滾、可驗證、可快速 merge」。

---

## 每顆結論（可行性）
- PR10: **GO**
- PR11: **GO（相容前提下）**
- PR12: **GO（中風險，需嚴守 contract）**

---

## 執行策略（簡版）
- 每顆 PR 都維持 `cache_manager.py` façade 不變。
- 每顆 PR 合併前都跑 `uv run pytest`。
- 嚴禁跨 PR 偷渡重構（一次一顆，確保回歸定位清楚）。