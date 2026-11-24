import pandas as pd

MIN_RR = 2.0
SWEEP_LOOKBACK = 12


def _resample(raw, minutes, now):
    d = raw.set_index("datetime").sort_index()
    b = d.resample(f"{minutes}min", label="left", closed="left").agg(
        {"open":"first","high":"max","low":"min","close":"last"}).dropna()
    return b[b.index + pd.Timedelta(minutes=minutes) <= now]


def _pivots(df, left=2, right=2):
    hi, lo = [], []
    for i in range(left, len(df)-right):
        w = df.iloc[i-left:i+right+1]
        if df.high.iloc[i] == w.high.max(): hi.append((i,float(df.high.iloc[i])))
        if df.low.iloc[i] == w.low.min(): lo.append((i,float(df.low.iloc[i])))
    return hi, lo


def _structure(df, left=2, right=2):
    h,l = _pivots(df,left,right)
    if len(h)<2 or len(l)<2: return "unclear",h,l
    if h[-1][1]>h[-2][1] and l[-1][1]>l[-2][1]: return "bullish",h,l
    if h[-1][1]<h[-2][1] and l[-1][1]<l[-2][1]: return "bearish",h,l
    return "unclear",h,l


def _atr(df):
    p=df.close.shift(1)
    tr=pd.concat([df.high-df.low,(df.high-p).abs(),(df.low-p).abs()],axis=1).max(axis=1)
    return tr.rolling(14).mean()


def _bos_zones(df,left=2,right=2,lookback=120):
    """A zone exists only after displacement closes beyond a previously confirmed pivot."""
    hs,ls=_pivots(df,left,right); atr=_atr(df); out=[]
    for i in range(max(left+right+2,len(df)-lookback),len(df)):
        a=atr.iloc[i]
        if pd.isna(a) or a<=0 or abs(float(df.close.iloc[i]-df.open.iloc[i]))<1.2*float(a): continue
        ph=next((p for p in reversed(hs) if p[0]+right<i and p[0]>=i-30),None)
        pl=next((p for p in reversed(ls) if p[0]+right<i and p[0]>=i-30),None)
        if ph and df.close.iloc[i]>df.open.iloc[i] and df.close.iloc[i]>ph[1]:
            origins=[j for j in range(max(0,i-6),i) if df.close.iloc[j]<df.open.iloc[j]]
            if origins:
                j=origins[-1]; out.append({"side":"demand","low":float(df.low.iloc[j]),"high":float(df.open.iloc[j]),"created":i,"created_time":df.index[i].isoformat(),"bos_level":ph[1]})
        if pl and df.close.iloc[i]<df.open.iloc[i] and df.close.iloc[i]<pl[1]:
            origins=[j for j in range(max(0,i-6),i) if df.close.iloc[j]>df.open.iloc[j]]
            if origins:
                j=origins[-1]; out.append({"side":"supply","low":float(df.open.iloc[j]),"high":float(df.high.iloc[j]),"created":i,"created_time":df.index[i].isoformat(),"bos_level":pl[1]})
    return out


def _invalid(df,z):
    later=df.iloc[z["created"]+1:]
    return bool((later.close>z["high"]).any()) if z["side"]=="supply" else bool((later.close<z["low"]).any())


def _wait(symbol,reason):
    return {"symbol":symbol,"action":"WAIT","reason":reason}


def _entry_sequence(m5,direction,z15):
    ztime=pd.Timestamp(z15["created_time"]) + pd.Timedelta(minutes=15)
    touched=[i for i,t in enumerate(m5.index) if t>=ztime and m5.low.iloc[i]<=z15["high"] and m5.high.iloc[i]>=z15["low"]]
    if not touched: return None
    first=touched[0]; highs,lows=_pivots(m5,2,2)
    for si in range(max(first+1,len(m5)-36),len(m5)-2):
        sw=m5.iloc[si]; prior=m5.iloc[max(first,si-SWEEP_LOOKBACK):si]
        if len(prior)<3: continue
        if direction=="sell":
            level=float(prior.high.max())
            if sw.high<=level or sw.close>=level: continue
            piv=[p for p in lows if first<=p[0]<si and p[0]+2<si]
            if not piv: continue
            mss=piv[-1][1]
            shifts=[k for k in range(si+1,len(m5)) if m5.close.iloc[k]<mss]
            if not shifts: continue
            mi=shifts[0]; origins=[j for j in range(si,mi) if m5.close.iloc[j]>m5.open.iloc[j]]
            if not origins: continue
            oi=origins[-1]; zl,zh=float(m5.open.iloc[oi]),float(m5.high.iloc[oi])
            pb=m5.iloc[mi+1:]
            if pb.empty or not ((pb.high>=zl)&(pb.low<=zh)).any(): continue
            last=m5.iloc[-1]
            if last.high<zl or last.low>zh: continue
            return {"zone_low":zl,"zone_high":zh,"sweep_extreme":float(sw.high),"sweep_time":m5.index[si].isoformat(),"shift_time":m5.index[mi].isoformat(),"mss_level":mss}
        else:
            level=float(prior.low.min())
            if sw.low>=level or sw.close<=level: continue
            piv=[p for p in highs if first<=p[0]<si and p[0]+2<si]
            if not piv: continue
            mss=piv[-1][1]
            shifts=[k for k in range(si+1,len(m5)) if m5.close.iloc[k]>mss]
            if not shifts: continue
            mi=shifts[0]; origins=[j for j in range(si,mi) if m5.close.iloc[j]<m5.open.iloc[j]]
            if not origins: continue
            oi=origins[-1]; zl,zh=float(m5.low.iloc[oi]),float(m5.open.iloc[oi])
            pb=m5.iloc[mi+1:]
            if pb.empty or not ((pb.low<=zh)&(pb.high>=zl)).any(): continue
            last=m5.iloc[-1]
            if last.high<zl or last.low>zh: continue
            return {"zone_low":zl,"zone_high":zh,"sweep_extreme":float(sw.low),"sweep_time":m5.index[si].isoformat(),"shift_time":m5.index[mi].isoformat(),"mss_level":mss}
    return None


def evaluate(symbol,raw_5m,now):
    m5,m15,h1=(_resample(raw_5m,n,now) for n in (5,15,60))
    if len(m5)<100 or len(m15)<40 or len(h1)<40: return _wait(symbol,"Insufficient closed candles")
    swing,hh,hl=_structure(h1,3,3); internal,_,_=_structure(h1,1,1)
    if swing=="unclear" or internal=="unclear": return _wait(symbol,"1h swing or internal structure unclear")
    # Internal direction is tradable; swing remains the higher-timeframe context.
    direction="buy" if swing=="bullish" else "sell"
    if internal != swing: return _wait(symbol,"1h swing and internal structures do not align")
    if len(hh)<2 or len(hl)<2: return _wait(symbol,"Insufficient confirmed 1h swing points")
    midpoint=(hh[-1][1]+hl[-1][1])/2
    side="demand" if direction=="buy" else "supply"
    candidates=[]
    for z in _bos_zones(m15):
        if z["side"]!=side or _invalid(m15,z): continue
        if direction=="buy" and z["high"]>=midpoint: continue
        if direction=="sell" and z["low"]<=midpoint: continue
        candidates.append(z)
    if not candidates: return _wait(symbol,f"No fresh 15m {side} zone with displacement, confirmed BOS and correct midpoint location")
    z15=candidates[-1]
    setup=_entry_sequence(m5,direction,z15)
    if not setup: return _wait(symbol,"Missing ordered sequence: 15m zone touch → 5m liquidity sweep → candle-close MSS → pullback")
    last=m5.iloc[-1]
    if direction=="sell" and last.close>z15["high"]: return _wait(symbol,"15m supply invalidated by candle close")
    if direction=="buy" and last.close<z15["low"]: return _wait(symbol,"15m demand invalidated by candle close")
    entry=(setup["zone_low"]+setup["zone_high"])/2
    stop=max(setup["sweep_extreme"],setup["zone_high"]) if direction=="sell" else min(setup["sweep_extreme"],setup["zone_low"])
    risk=abs(stop-entry)
    if risk<=0: return _wait(symbol,"Invalid stop distance")
    two_r=entry-2*risk if direction=="sell" else entry+2*risk
    # Use the nearest real opposing 5m BOS zone, not a raw pivot; otherwise target 2R.
    opp="demand" if direction=="sell" else "supply"; candidates5=[]
    for z in _bos_zones(m5,lookback=180):
        if z["side"]!=opp or _invalid(m5,z): continue
        level=z["high"] if direction=="buy" else z["low"]
        if (direction=="buy" and level>entry) or (direction=="sell" and level<entry):
            candidates5.append((abs(level-entry),level))
    candidates5.sort()
    if candidates5:
        target=candidates5[0][1]
        if abs(target-entry)<2*risk: return _wait(symbol,"Nearest opposing 5m zone is closer than 2R; skip")
        target_type="next opposing 5m zone"
    else: target=two_r; target_type="2R"
    atr=float(_atr(m5).iloc[-1])
    if pd.isna(atr) or atr<=0: return _wait(symbol,"Cannot estimate review window")
    minutes=abs(target-entry)/atr*5
    hold=5 if minutes<=300 else 12 if minutes<=720 else 24
    action="BUY" if direction=="buy" else "SELL"
    return {"symbol":symbol,"action":action,"price":entry,"entry_zone_low":setup["zone_low"],"entry_zone_high":setup["zone_high"],"stop_loss":stop,"take_profit_1":entry+(risk if direction=="buy" else -risk),"take_profit_2":target,"target_type":target_type,"risk_distance":risk,"risk_reward":round(abs(target-entry)/risk,2),"swing_trend_1h":swing,"internal_trend_1h":internal,"trend_aligned":swing==internal,"zone_15m":z15,"liquidity_sweep":"confirmed","market_shift":"5m candle close confirmed","setup_details":setup,"hold_hours":hold,"hold_window_estimated_minutes":round(minutes),"hold_window":"Estimated review window only; not guaranteed","candle_time":m5.index[-1].isoformat(),"valid_until":(m5.index[-1] + pd.Timedelta(minutes=5)).isoformat(),"reason":f"1h swing {swing}; 1h internal {internal}; 15m {side} BOS zone in correct half; 5m zone touch, sweep, close-confirmed MSS, pullback; {target_type}; RR >= 1:2"}
