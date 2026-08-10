//+------------------------------------------------------------------+
//| Deterministic.mqh - klasifikasi regime & sinyal deterministik     |
//| WAJIB sinkron dengan python/regime.py (classify +                 |
//| deterministic_signal). Dipakai sebagai:                           |
//|   1) fallback live bila bridge/LLM mati                           |
//|   2) engine di strategy tester (backtest baseline, tanpa network) |
//+------------------------------------------------------------------+
#property strict

enum RegimeLabel { REG_TREND_UP=0, REG_TREND_DOWN, REG_RANGING, REG_CHOPPY, REG_MIXED, REG_COUNT };

struct RegimeResult
{
   RegimeLabel label;
   double      confidence;
   double      scores[5];   // urutan sesuai RegimeLabel
};

//--- threshold & parameter (mirror config.yaml)
struct DConfig
{
   int    atr_period;
   int    adx_period;
   int    ema_fast;
   int    ema_slow;
   int    rsi_period;
   int    er_period;
   double trend_er_min;
   double trend_adx_min;
   double range_er_max;
   double range_adx_max;
   double highvol_percentile;
   double min_confidence;
   double rr_target;
   double atr_sl_mult;
   double max_lot;
   double min_lot;
   double risk_pct;        // fraksi equity per trade (0.005 = 0.5%)
   double tick_size;
   double tick_value;
   double lot_step;        // kelipatan lot broker (0.01 default)
   //--- vol-target & confidence scaling (mirror config.yaml risk:)
   bool   vol_target_enabled;
   double vol_target_atr;      // ATR% target
   double vt_min_mult;
   double vt_max_mult;
   bool   conf_scaling_enabled;
   double conf_base;
   double cs_min_mult;
   double cs_max_mult;
   //--- entry (mirror config.yaml entry:)
   int    entry_type;      // 0=market 1=pullback 2=breakout
   int    swing_n;
   int    breakout_lookback;
   double pullback_ema_dist_pct;
   bool   require_htf_alignment;
   int    min_htf_agree;
   //--- session (mirror config.yaml session:)
   bool   session_enabled;
   int    session_entry_start;   // jam (0-23)
   int    session_entry_end;
   int    session_quiet_start;   // -1 = nonaktif
   int    session_quiet_end;
   int    server_offset_hours;
};

enum SigAction { SIG_HOLD=0, SIG_OPEN, SIG_CLOSE, SIG_MODIFY };

struct SignalResult
{
   SigAction action;
   int       side;        // 1=long -1=short 0=flat
   double    entry;
   double    sl;
   double    tp;
   double    lots;
   string    reason;
};

enum EntryType { E_MARKET=0, E_PULLBACK=1, E_BREAKOUT=2 };

class CDeterministic
{
public:
   //--- softmax over 5 scores
   static void Softmax(double &scores[])
   {
      double mx=scores[0];
      for(int i=1;i<5;i++) mx=MathMax(mx,scores[i]);
      double sum=0.0;
      for(int i=0;i<5;i++) { scores[i]=MathExp(scores[i]-mx); sum+=scores[i]; }
      if(sum<1e-12) sum=1e-12;
      for(int i=0;i<5;i++) scores[i]/=sum;
   }

   //--- klasifikasi regime (mirror regime.py classify)
   static void Classify(FeatureSnapshot &primary,
                        FeatureSnapshot &higher[], int higherCount,
                        const DConfig &cfg,
                        RegimeResult &out)
   {
      double raw[5];
      int i;
      for(i=0;i<5;i++) raw[i]=0.0;

      double er=primary.er, adx=primary.adx;
      bool emaBull=primary.ema_bull;
      double hv=primary.atr_pct_percentile;

      // context trend dari TF lebih tinggi: mayoritas ema_bull
      bool ctxKnown=false, ctxBull=false;
      int bull=0;
      for(i=0;i<higherCount;i++)
      {
         if(higher[i].close>0) { bull+=higher[i].ema_bull?1:0; }
      }
      if(higherCount>0) { ctxKnown=true; ctxBull=(bull>= (higherCount+1)/2); }

      if(er>cfg.trend_er_min && adx>cfg.trend_adx_min)
      {
         int idx=emaBull?0:1;   // TREND_UP / TREND_DOWN
         raw[idx]=1.0+(er-cfg.trend_er_min)*2.0+(adx-cfg.trend_adx_min)/30.0;
         if(ctxKnown)
         {
            if(emaBull==ctxBull) raw[idx]+=0.5;
            else raw[idx]-=0.5;
         }
      }
      else if(er<cfg.range_er_max && adx<cfg.range_adx_max && hv<cfg.highvol_percentile)
         raw[2]=1.0+(cfg.range_er_max-er)+(cfg.range_adx_max-adx)/30.0;
      else if(hv>=cfg.highvol_percentile && er<cfg.trend_er_min)
         raw[3]=1.0+(hv-cfg.highvol_percentile)*3.0;
      else
         raw[4]=1.0;

      double scores[5];
      for(i=0;i<5;i++) scores[i]=raw[i];
      Softmax(scores);

      out.confidence=scores[0];
      out.label=REG_TREND_UP;
      for(i=1;i<5;i++) if(scores[i]>out.confidence) { out.confidence=scores[i]; out.label=(RegimeLabel)i; }
      for(i=0;i<5;i++) out.scores[i]=scores[i];
   }

   //--- session filter (mirror regime.py _hour_ok)
   static bool HourOk(datetime barTime, const DConfig &cfg)
   {
      if(barTime<=0 || !cfg.session_enabled) return true;
      MqlDateTime dt;
      TimeToStruct(barTime,dt);
      int h=(dt.hour + cfg.server_offset_hours + 48)%24;
      int qs=cfg.session_quiet_start, qe=cfg.session_quiet_end;
      if(qs>=0 && qe>=0)
      {
         if(qs<=qe) { if(h>=qs && h<qe) return false; }
         else       { if(h>=qs || h<qe) return false; }
      }
      int es=cfg.session_entry_start, ee=cfg.session_entry_end;
      if(es<=ee) { if(!(h>=es && h<ee)) return false; }
      else       { if(!(h>=es || h<ee)) return false; }
      return true;
   }

   //--- HTF alignment gate (mirror regime.py _htf_aligned)
   static bool HtfAligned(FeatureSnapshot &higher[], int higherCount,
                          bool wantLong, const DConfig &cfg)
   {
      if(higherCount<=0) return true;
      int req=MathMax(1,MathMin(cfg.min_htf_agree,higherCount));
      int agree=0;
      for(int i=0;i<higherCount;i++) if(higher[i].ema_bull==wantLong) agree++;
      return agree>=req;
   }

   //--- tipe entry (mirror regime.py _entry_ok)
   static bool EntryOk(FeatureSnapshot &f, const DConfig &cfg, bool wantLong)
   {
      if(cfg.entry_type==E_MARKET) return true;
      if(cfg.entry_type==E_PULLBACK)
      {
         double cvs=f.close_vs_ema_slow;
         if(wantLong) return f.last_bar_down && f.near_ema_fast && cvs>-0.5;
         return f.last_bar_up && f.near_ema_fast && cvs<0.5;
      }
      if(cfg.entry_type==E_BREAKOUT) return wantLong ? f.breakout_high : f.breakout_low;
      return true;
   }

   //--- sizing terpusat (mirror regime.py compute_lot)
   static double ComputeLot(double equity,double slDist,const DConfig &cfg,
                            double atrPct=0.0,double confidence=0.0)
   {
      double mult=1.0;
      if(cfg.vol_target_enabled && atrPct>0.0)
      {
         double m=cfg.vol_target_atr/MathMax(atrPct,1e-9);
         mult*=MathMax(cfg.vt_min_mult,MathMin(cfg.vt_max_mult,m));
      }
      if(cfg.conf_scaling_enabled && confidence>0.0)
      {
         double m=confidence/MathMax(cfg.conf_base,1e-9);
         mult*=MathMax(cfg.cs_min_mult,MathMin(cfg.cs_max_mult,m));
      }
      double riskAmt=equity*cfg.risk_pct*mult;
      double perPoint=slDist*cfg.tick_value/cfg.tick_size;
      double lot=perPoint>0 ? riskAmt/perPoint : 0.0;
      double step=cfg.lot_step>0 ? cfg.lot_step : 0.01;
      lot=MathRound(lot/step)*step;
      lot=MathMax(lot,cfg.min_lot>0?cfg.min_lot:0.01);
      lot=MathMin(lot,cfg.max_lot);
      return lot;
   }

   //--- sinyal trading deterministik (mirror regime.py deterministic_signal)
   static void Signal(FeatureSnapshot &primary,
                      FeatureSnapshot &higher[], int higherCount,
                      const RegimeResult &regime,
                      const DConfig &cfg,
                      double equity,
                      double spread,
                      datetime barTime,
                      SignalResult &out)
   {
      out.action=SIG_HOLD; out.side=0; out.entry=0; out.sl=0; out.tp=0; out.lots=0;
      out.reason="no signal";

      if(regime.label!=REG_TREND_UP && regime.label!=REG_TREND_DOWN)
      {
         if(regime.label==REG_RANGING || regime.label==REG_CHOPPY)
            out.reason=(regime.label==REG_RANGING?"RANGING":"CHOPPY")+", wait for trend setup";
         return;
      }
      bool wantLong=regime.label==REG_TREND_UP;
      if(regime.confidence<cfg.min_confidence)
      {
         out.reason=StringFormat("%s conf=%.2f < %.2f",
                                 wantLong?"TREND_UP":"TREND_DOWN",
                                 regime.confidence,cfg.min_confidence);
         return;
      }
      if(!HourOk(barTime,cfg)) { out.reason="session filter => skip"; return; }
      if(cfg.require_htf_alignment && !HtfAligned(higher,higherCount,wantLong,cfg))
      { out.reason="HTF tidak searah => skip"; return; }
      if(!EntryOk(primary,cfg,wantLong))
      {
         string t=cfg.entry_type==E_PULLBACK?"pullback":(cfg.entry_type==E_BREAKOUT?"breakout":"market");
         out.reason="entry type '"+t+"' tidak terpenuhi";
         return;
      }

      double entry=primary.close;
      double atr=primary.atr_pct/100.0*primary.close;
      double slDist=MathMax(cfg.atr_sl_mult*atr, spread*2.0);
      double sl,tp;
      int side;
      if(wantLong) { sl=entry-slDist; tp=entry+slDist*cfg.rr_target; side=1; }
      else         { sl=entry+slDist; tp=entry-slDist*cfg.rr_target; side=-1; }
      double lot=ComputeLot(equity,slDist,cfg,primary.atr_pct,regime.confidence);
      out.action=SIG_OPEN; out.side=side; out.entry=NormalizeDouble(entry,_Digits);
      out.sl=NormalizeDouble(sl,_Digits); out.tp=NormalizeDouble(tp,_Digits); out.lots=lot;
      out.reason=(wantLong?"TREND_UP":"TREND_DOWN")+
                 StringFormat(" conf=%.2f adx=%.1f er=%.2f",regime.confidence,primary.adx,primary.er);
   }
};
