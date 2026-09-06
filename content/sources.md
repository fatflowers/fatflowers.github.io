---
title: "Intelligence sources"
description: "The targets, channels and collection settings behind the intelligence reports."
layout: "sources"
translationKey: "intelligence-sources"
hideMeta: true
ShowToc: false
---

This is a **configured snapshot**, generated from the same catalog used by the collection pipeline at each site build—not a live health dashboard. Enabled means scheduled for collection, not that the latest fetch succeeded or that every item will appear in a report.

Sources are organized as **target → channel**, with tags across both levels. Collection intervals are configured minimums; actual execution also depends on the scheduler, upstream availability and retries. Reports select, verify and deduplicate collected items.
