//+------------------------------------------------------------------+
//| RegimeEA.mq5                                                      |
//| EA hybrid: eksekusi tipis + bridge Python (LLM DeepSeek) di live, |
//| fallback deterministik (parity python) di tester / saat bridge    |
//| mati. Keputusan dibuat per-close bar timeframe decision.          |
//|                                                                   |
//| SETUP WAJIB:                                                      |
//|  Tools -> Options -> Expert Advisors -> Allow WebRequest:         |
//|   tambahkan http://127.0.0.1:8080                                 |
//|  Jalankan bridge Python: cd python && uvicorn server:app --port 8080|
//+------------------------------------------------------------------+
#property strict
#property copyright "forex_algo"
#property version   "1.00"

#include <Trade\Trade.mqh>
#include "include\Json.mqh"
#include "include\Features.mqh"
#include "include\Deterministic.mqh"
#include "include\Bridge.mqh"

input group "=== Bridge ==="
input string            InpServerUrl        = "http://127.0.0.1:8080";   // URL bridge (whitelist di WebRequest)
input int               InpTimeoutMs        = 15000;                     // timeout WebRequest (ms)
input bool              InpEnableLLM        = true;                      // true=live LLM, false=deterministic
input int               InpBarsPerTF        = 60;                        // jumlah bar per TF yang dikirim
input int               InpFallbackAfter    = 3;                         // N gagal berturut -> deterministic
input int               InpRetryBars        = 50;                        // tiap N bar coba bridge lagi (auto-recovery)
input bool              InpSignalOnly       = false;                     // true = log decision tanpa eksekusi (A/B)
input string            InpBridgeToken      = "";                        // token remote Linux bridge

input group "=== Decision ==="
input ENUM_TIMEFRAMES   InpDecisionTF       = PERIOD_M5;                 // TF keputusan

input group "=== Risk ==="
input double            InpRiskPct          = 0.5;                       // % equity per trade
input double            InpMaxLot           = 1.0;
input double            InpMinLot           = 0.01;
input double            InpLotStep          = 0.01;                      // kelipatan lot broker
input int               InpMaxOpenPositions = 3;
input double            InpMaxTotalRiskPct  = 2.0;                       // total risk lintas posisi
input double            InpMaxSpreadPct     = 0.05;                      // spread maks (pct harga)
input long              InpMagic            = 20240806;

input group "=== Sizing (parity config.yaml risk:) ==="
input bool              InpVolTarget        = false;                     // skala lot vs volatilitas
input double            InpVolTargetAtr     = 0.10;                      // ATR% target
input double            InpVtMinMult        = 0.5;
input double            InpVtMaxMult        = 2.0;
input bool              InpConfScaling      = false;                     // risk naik seiring confidence
input double            InpConfBase         = 0.70;
input double            InpCsMinMult        = 0.5;
input double            InpCsMaxMult        = 1.5;

input group "=== Entry (parity config.yaml) ==="
input EntryType         InpEntryType        = E_PULLBACK;                // market | pullback | breakout
input int               InpSwingN           = 20;
input int               InpBreakoutLookback = 3;
input double            InpPullbackEmaDist  = 0.15;                      // close maks X% dari ema_slow
input bool              InpRequireHtf       = false;                     // wajib HTF searah
input int               InpMinHtfAgree      = 2;

input group "=== Session (parity config.yaml) ==="
input bool              InpSessionEnabled   = false;
input int               InpSessionStart     = 7;                         // jam entry mulai (server)
input int               InpSessionEnd       = 21;                        // jam entry akhir
input int               InpQuietStart       = -1;                        // -1 = nonaktif (blokir)
input int               InpQuietEnd         = -1;
input int               InpServerOffset     = 0;

input group "=== Deterministic (parity python/config.yaml) ==="
input int               InpAtrPeriod        = 14;
input int               InpAdxPeriod        = 14;
input int               InpEmaFast          = 9;
input int               InpEmaSlow          = 21;
input int               InpRsiPeriod        = 14;
input int               InpErPeriod         = 20;
input double            InpTrendErMin       = 0.35;
input double            InpTrendAdxMin      = 22.0;
input double            InpRangeErMax       = 0.18;
input double            InpRangeAdxMax      = 20.0;
input double            InpHvPercentile     = 0.80;
input double            InpMinConfidence    = 0.60;
input double            InpRR               = 1.5;
input double            InpAtrSlMult        = 1.5;

CTrade trade;
MqlRates gM5[], gM15[], gH1[], gH4[];
string   gTimeframeName="M5";
int      gFallbackCount=0;
bool     gDeterministicMode=false;
int      gSinceFallback=0;
string   gLastEngine="", gLastAction="", gLastReasoning="", gLastRegime="";
double   gLastConfidence=0;

//+------------------------------------------------------------------+
ENUM_TIMEFRAMES MinutesToPeriod(int min)
{
   switch(min)
   {
      case 1:    return PERIOD_M1;
      case 5:    return PERIOD_M5;
      case 15:   return PERIOD_M15;
      case 30:   return PERIOD_M30;
      case 60:   return PERIOD_H1;
      case 120:  return PERIOD_H2;
      case 240:  return PERIOD_H4;
      case 480:  return PERIOD_H8;
      case 1440: return PERIOD_D1;
   }
   return PERIOD_H1;
}

//+------------------------------------------------------------------+
void FillChrono(ENUM_TIMEFRAMES tf,int count,MqlRates &out[])
{
   MqlRates tmp[];
   int n=CopyRates(_Symbol,tf,0,count,tmp);
   if(n<=0) { ArrayResize(out,0); return; }
   ArrayResize(out,n);
   for(int i=0;i<n;i++) out[i]=tmp[n-1-i];   // kronologis: index 0 tertua
}

//+------------------------------------------------------------------+
bool ExtractFeatures(const MqlRates &rates[],const DConfig &cfg,FeatureSnapshot &f)
{
   int n=ArraySize(rates);
   if(n<60) return false;
   double open[],high[],low[],close[],volume[];
   ArrayResize(open,n);ArrayResize(high,n);ArrayResize(low,n);
   ArrayResize(close,n);ArrayResize(volume,n);
   for(int i=0;i<n;i++)
   {
      open[i]=rates[i].open; high[i]=rates[i].high; low[i]=rates[i].low;
      close[i]=rates[i].close; volume[i]=(double)rates[i].tick_volume;
   }
   return FE_Compute(open,high,low,close,volume,
                     (int)cfg.atr_period,(int)cfg.adx_period,20,
                     (int)cfg.ema_fast,(int)cfg.ema_slow,(int)cfg.rsi_period,
                     (int)cfg.er_period,f,
                     cfg.swing_n,cfg.breakout_lookback,cfg.pullback_ema_dist_pct);
}

//+------------------------------------------------------------------+
double AtrFromRates(const MqlRates &rates[], int period)
{
   int n=ArraySize(rates);
   if(n<=period) return 0.0;
   double open[],high[],low[],close[],volume[],atr[];
   ArrayResize(open,n);ArrayResize(high,n);ArrayResize(low,n);
   ArrayResize(close,n);ArrayResize(volume,n);
   for(int i=0;i<n;i++)
   {
      open[i]=rates[i].open; high[i]=rates[i].high;
      low[i]=rates[i].low; close[i]=rates[i].close;
      volume[i]=(double)rates[i].tick_volume;
   }
   FE_Atr(high,low,close,period,atr);
   return atr[n-1];
}

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(10);
   trade.SetTypeFillingBySymbol(_Symbol);

   int min=PeriodSeconds(InpDecisionTF)/60;
   gTimeframeName=StringFormat("M%d",min);
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   Comment("");
}

//+------------------------------------------------------------------+
void OnTick()
{
   static datetime lastBar=0;
   datetime cur=iTime(_Symbol,InpDecisionTF,0);
   if(cur==lastBar) return;   // sekali per bar close
   lastBar=cur;

   FillChrono(InpDecisionTF,InpBarsPerTF,gM5);
   int mult=PeriodSeconds(InpDecisionTF)/60;
   FillChrono(MinutesToPeriod(mult*3),InpBarsPerTF,gM15);
   FillChrono(MinutesToPeriod(mult*12),InpBarsPerTF,gH1);
   FillChrono(MinutesToPeriod(mult*48),InpBarsPerTF,gH4);

   // auto-recovery: jika bridge sudah lama mati, coba lagi berkala
   if(gDeterministicMode)
   {
      gSinceFallback++;
      if(gSinceFallback>=InpRetryBars)
      {
         gDeterministicMode=false;
         gSinceFallback=0;
         Print("RegimeEA: mencoba bridge lagi (auto-recovery)");
      }
   }

   if(MQLInfoInteger(MQL_TESTER) || !InpEnableLLM || gDeterministicMode)
      RunDeterministic();
   else
      RunBridge();
   RefreshComment();
}

//+------------------------------------------------------------------+
void RunDeterministic()
{
   DConfig cfg;
   FillConfig(cfg);
   gLastEngine="deterministic";

   FeatureSnapshot fMain,fHigher[3];
   bool ok=ExtractFeatures(gM5,cfg,fMain);
   if(!ok) { gLastAction="HOLD"; gLastReasoning="insufficient data"; return; }
   int hCount=0;
   if(ExtractFeatures(gM15,cfg,fHigher[hCount])) hCount++;
   if(ExtractFeatures(gH1,cfg,fHigher[hCount])) hCount++;
   if(ExtractFeatures(gH4,cfg,fHigher[hCount])) hCount++;

   RegimeResult regime;
   CDeterministic::Classify(fMain,fHigher,hCount,cfg,regime);
   gLastRegime=RegimeName(regime.label);
   gLastConfidence=regime.confidence;

   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double spread=SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID);
   datetime barTime=iTime(_Symbol,InpDecisionTF,1);   // bar yang baru saja close
   SignalResult sig;
   CDeterministic::Signal(fMain,fHigher,hCount,regime,cfg,equity,spread,barTime,sig);
   gLastAction=SigActionName(sig.action);
   gLastReasoning=sig.reason;
   if(!InpSignalOnly) ExecuteSignal(sig);
}

//+------------------------------------------------------------------+
void RunBridge()
{
   double balance=AccountInfoDouble(ACCOUNT_BALANCE);
   double equity=AccountInfoDouble(ACCOUNT_EQUITY);
   double tickSize=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   double spread=SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID);
   ulong reqId=(ulong)(TimeCurrent()*1000 + GetTickCount()%1000);

   string payload=CBridge::BuildRequest(_Symbol,gTimeframeName,gM5,gM15,gH1,gH4,
                                        balance,equity,tickSize,tickValue,spread,reqId,InpBridgeToken);
   BridgeDecision dec;
   if(!CBridge::Send(InpServerUrl,payload,InpTimeoutMs,dec))
   {
      gFallbackCount++;
      gLastEngine="bridge-error";
      string err=dec.raw;
      if(gFallbackCount>=InpFallbackAfter)
      {
         gDeterministicMode=true;
         Print("RegimeEA: fallback permanen ke deterministic setelah ",IntegerToString(gFallbackCount),"x gagal");
      }
      RunDeterministic();
      gLastEngine="bridge-error";
      gLastReasoning="bridge down ("+err+") | "+gLastReasoning;
      return;
   }
   CBridge::Parse(dec.raw,dec);
   gFallbackCount=0;
   gLastEngine=dec.engine;
   gLastRegime=dec.regime_label;
   gLastConfidence=dec.confidence;
   gLastAction=dec.action;
   gLastReasoning=dec.reasoning;

   if(dec.action=="OPEN")
   {
      int side=dec.bias=="SHORT" ? -1 : 1;
      double lots=MathMin(dec.lots,InpMaxLot);
      if(lots<0.01) lots=0.01;
      // fallback SL/TP dari ATR bila bridge tidak menyediakan (anti posisi tanpa SL)
      if(dec.sl<=0.0 || dec.tp<=0.0)
      {
         double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK);
         double bid=SymbolInfoDouble(_Symbol,SYMBOL_BID);
         double atr=AtrFromRates(gM5,InpAtrPeriod);
         double slDist=MathMax(InpAtrSlMult*atr, (ask-bid)*2.0);
         if(dec.sl<=0.0) dec.sl=side>0 ? NormalizeDouble(bid-slDist,_Digits)
                                        : NormalizeDouble(ask+slDist,_Digits);
         if(dec.tp<=0.0) dec.tp=side>0 ? NormalizeDouble(dec.sl+slDist*InpRR,_Digits)
                                       : NormalizeDouble(dec.sl-slDist*InpRR,_Digits);
      }
      SignalResult sig;
      sig.action=SIG_OPEN; sig.side=side; sig.entry=0;
      sig.sl=dec.sl; sig.tp=dec.tp; sig.lots=lots;
      if(!InpSignalOnly) ExecuteSignal(sig);
   }
   else if(dec.action=="CLOSE")
   {
      if(!InpSignalOnly) ExecuteCloseAll();
   }
   else if(dec.action=="MODIFY")
   {
      if(!InpSignalOnly) ExecuteModify(dec.sl,dec.tp);
   }
   // HOLD -> tidak ada aksi
}

//+------------------------------------------------------------------+
void FillConfig(DConfig &cfg)
{
   cfg.atr_period=InpAtrPeriod;
   cfg.adx_period=InpAdxPeriod;
   cfg.ema_fast=InpEmaFast;
   cfg.ema_slow=InpEmaSlow;
   cfg.rsi_period=InpRsiPeriod;
   cfg.er_period=InpErPeriod;
   cfg.trend_er_min=InpTrendErMin;
   cfg.trend_adx_min=InpTrendAdxMin;
   cfg.range_er_max=InpRangeErMax;
   cfg.range_adx_max=InpRangeAdxMax;
   cfg.highvol_percentile=InpHvPercentile;
   cfg.min_confidence=InpMinConfidence;
   cfg.rr_target=InpRR;
   cfg.atr_sl_mult=InpAtrSlMult;
   cfg.max_lot=InpMaxLot;
   cfg.min_lot=InpMinLot;
   cfg.risk_pct=InpRiskPct/100.0;
   cfg.tick_size=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_SIZE);
   cfg.tick_value=SymbolInfoDouble(_Symbol,SYMBOL_TRADE_TICK_VALUE);
   cfg.lot_step=InpLotStep>0?InpLotStep:0.01;
   // vol-target & confidence scaling
   cfg.vol_target_enabled=InpVolTarget;
   cfg.vol_target_atr=InpVolTargetAtr;
   cfg.vt_min_mult=InpVtMinMult;
   cfg.vt_max_mult=InpVtMaxMult;
   cfg.conf_scaling_enabled=InpConfScaling;
   cfg.conf_base=InpConfBase;
   cfg.cs_min_mult=InpCsMinMult;
   cfg.cs_max_mult=InpCsMaxMult;
   // entry
   cfg.entry_type=InpEntryType;
   cfg.swing_n=MathMax(InpSwingN,1);
   cfg.breakout_lookback=MathMax(InpBreakoutLookback,1);
   cfg.pullback_ema_dist_pct=InpPullbackEmaDist;
   cfg.require_htf_alignment=InpRequireHtf;
   cfg.min_htf_agree=MathMax(InpMinHtfAgree,1);
   // session
   cfg.session_enabled=InpSessionEnabled;
   cfg.session_entry_start=InpSessionStart;
   cfg.session_entry_end=InpSessionEnd;
   cfg.session_quiet_start=InpQuietStart;
   cfg.session_quiet_end=InpQuietEnd;
   cfg.server_offset_hours=InpServerOffset;
}

//+------------------------------------------------------------------+
int CountPositions(string symbol)
{
   int cnt=0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)==symbol) cnt++;
   }
   return cnt;
}

//+------------------------------------------------------------------+
// estimasi risk posisi (dalam mata uang akun) dari jarak SL
double PositionRisk(const string symbol, double lots, double slDistance)
{
   double tickSize=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_SIZE);
   double tickValue=SymbolInfoDouble(symbol,SYMBOL_TRADE_TICK_VALUE);
   if(tickSize<=0) tickSize=1e-9;
   return lots*slDistance/tickSize*tickValue;
}

double TotalOpenRisk(const string symbol)
{
   double total=0.0;
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=symbol) continue;
      double op=PositionGetDouble(POSITION_PRICE_OPEN);
      double sl=PositionGetDouble(POSITION_SL);
      double lots=PositionGetDouble(POSITION_VOLUME);
      if(sl<=0) continue;
      total+=PositionRisk(symbol,lots,MathAbs(op-sl));
   }
   return total;
}

//+------------------------------------------------------------------+
void ExecuteSignal(const SignalResult &sig)
{
   if(sig.action==SIG_OPEN)
   {
      if(CountPositions(_Symbol)>=InpMaxOpenPositions)
      {
         Print("RegimeEA: max open positions reached, skip OPEN");
         return;
      }
      double spreadPct=(SymbolInfoDouble(_Symbol,SYMBOL_ASK)-SymbolInfoDouble(_Symbol,SYMBOL_BID))/SymbolInfoDouble(_Symbol,SYMBOL_BID)*100.0;
      if(spreadPct>InpMaxSpreadPct)
      {
         Print("RegimeEA: spread terlalu lebar (",DoubleToString(spreadPct,3),"%), skip");
         return;
      }
      // validasi volume broker
      double vmin=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN);
      double vmax=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MAX);
      double vstep=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_STEP);
      double lots=sig.lots;
      if(vstep>0) lots=MathRound(lots/vstep)*vstep;
      lots=MathMax(lots,vmin>0?vmin:0.01);
      lots=MathMin(lots,vmax>0?vmax:InpMaxLot);
      // enforce total risk lintas posisi (estimasi risk baris ini dari SL vs harga)
      double entryRef = sig.side>0 ? SymbolInfoDouble(_Symbol,SYMBOL_ASK)
                                   : SymbolInfoDouble(_Symbol,SYMBOL_BID);
      double slDistNew = sig.sl>0 ? MathAbs(sig.sl-entryRef) : 0.0;
      double newRisk = PositionRisk(_Symbol,lots,slDistNew);
      double totalRisk=TotalOpenRisk(_Symbol)+newRisk;
      double riskCap=AccountInfoDouble(ACCOUNT_EQUITY)*InpMaxTotalRiskPct/100.0;
      if(totalRisk>riskCap && InpMaxTotalRiskPct>0)
      {
         Print("RegimeEA: total risk (",DoubleToString(totalRisk,2),
               ") > cap (",DoubleToString(riskCap,2),"), skip OPEN");
         return;
      }
      double sl=sig.sl, tp=sig.tp;
      bool ok;
      if(sig.side>0)      ok=trade.Buy(lots,_Symbol,0.0,sl,tp,"regime-llm");
      else                ok=trade.Sell(lots,_Symbol,0.0,sl,tp,"regime-llm");
      if(!ok) Print("RegimeEA: order failed: ",trade.ResultRetcodeDescription());
   }
   else if(sig.action==SIG_CLOSE)
      ExecuteCloseAll();
   else if(sig.action==SIG_MODIFY)
      ExecuteModify(sig.sl,sig.tp);
}

//+------------------------------------------------------------------+
void ExecuteCloseAll()
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      if(!trade.PositionClose(tk))
         Print("RegimeEA: close failed ",IntegerToString(tk)," ",trade.ResultRetcodeDescription());
   }
}

void ExecuteModify(double sl,double tp)
{
   for(int i=PositionsTotal()-1;i>=0;i--)
   {
      ulong tk=PositionGetTicket(i);
      if(tk==0) continue;
      if(PositionGetString(POSITION_SYMBOL)!=_Symbol) continue;
      double nsl=sl>0?sl:PositionGetDouble(POSITION_SL);
      double ntp=tp>0?tp:PositionGetDouble(POSITION_TP);
      trade.PositionModify(tk,nsl,ntp);
   }
}

//+------------------------------------------------------------------+
string RegimeName(RegimeLabel l)
{
   switch(l)
   {
      case REG_TREND_UP:   return "TREND_UP";
      case REG_TREND_DOWN: return "TREND_DOWN";
      case REG_RANGING:    return "RANGING";
      case REG_CHOPPY:     return "CHOPPY";
   }
   return "MIXED";
}

string SigActionName(SigAction a)
{
   switch(a)
   {
      case SIG_OPEN:  return "OPEN";
      case SIG_CLOSE: return "CLOSE";
      case SIG_MODIFY:return "MODIFY";
   }
   return "HOLD";
}

//+------------------------------------------------------------------+
void RefreshComment()
{
   string txt="RegimeEA v0.1.0\n";
   txt+="Engine   : "+gLastEngine+"\n";
   txt+="Regime   : "+gLastRegime+" ("+DoubleToString(gLastConfidence,2)+")\n";
   txt+="Action   : "+gLastAction+"\n";
   txt+="Reasoning: "+gLastReasoning+"\n";
   txt+="Bars     : "+IntegerToString(ArraySize(gM5))+" x M5\n";
   Comment(txt);
}
