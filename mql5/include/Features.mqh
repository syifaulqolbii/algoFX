//+------------------------------------------------------------------+
//| Features.mqh - fitur teknikal identik dengan python/features.py    |
//| WAJIB sinkron: apapun perubahan di sini harus diubah di Python.    |
//| Semua fungsi beroperasi pada array harga kronologis (bar 0 = tertua)|
//+------------------------------------------------------------------+
#property strict

struct FeatureSnapshot
{
   double close;
   double atr_pct;            // ATR / close * 100
   double adx;
   double er;                 // Kaufman efficiency ratio
   double rsi;
   double ema_fast;
   double ema_slow;
   bool   ema_bull;
   bool   ema_cross_recent;
   double vol_ratio;
   double range_pct;          // (high-low)/close*100
   double last_ret_pct;
   double atr_pct_percentile; // 0..1
   //--- entry context (parity python/features.py)
   double swing_high;
   double swing_low;
   double swing_mid;
   double close_vs_ema_slow;  // %
   bool   near_ema_fast;
   bool   breakout_high;
   bool   breakout_low;
   bool   last_bar_down;
   bool   last_bar_up;
};

//--- helper
void FE_ArrayResizeCopy(const double &src[], double &dst[])
{
   int n=ArraySize(src);
   ArrayResize(dst,n);
   int i;
   for(i=0;i<n;i++) dst[i]=src[i];
}

void FE_Ema(const double &src[], int period, double &out[])
{
   int n=ArraySize(src);
   ArrayResize(out,n);
   double a=2.0/(period+1);
   out[0]=src[0];
   for(int i=1;i<n;i++) out[i]=src[i]*a+out[i-1]*(1.0-a);
}

void FE_Ewm(const double &src[], int period, double &out[])
{
   int n=ArraySize(src);
   ArrayResize(out,n);
   double a=1.0/period;
   out[0]=src[0];
   for(int i=1;i<n;i++) out[i]=src[i]*a+out[i-1]*(1.0-a);
}

void FE_TrueRange(const double &high[], const double &low[], const double &close[], double &out[])
{
   int n=ArraySize(close);
   ArrayResize(out,n);
   out[0]=high[0]-low[0];
   for(int i=1;i<n;i++)
   {
      double hl=high[i]-low[i];
      double hc=MathAbs(high[i]-close[i-1]);
      double lc=MathAbs(low[i]-close[i-1]);
      out[i]=MathMax(hl,MathMax(hc,lc));
   }
}

void FE_Atr(const double &high[], const double &low[], const double &close[], int period, double &out[])
{
   int n=ArraySize(close);
   ArrayResize(out,n);
   double tr[];
   FE_TrueRange(high,low,close,tr);
   double a=1.0/period;
   double sum=0.0;
   for(int i=0;i<=period;i++) sum+=tr[i];
   out[period]=sum/(period+1);
   for(int i=period+1;i<n;i++) out[i]=out[i-1]*(1.0-a)+tr[i]*a;
   for(int i=0;i<=period;i++)  out[i]=out[period];
}

void FE_Adx(const double &high[], const double &low[], const double &close[], int period, double &out[])
{
   int n=ArraySize(close);
   ArrayResize(out,n);
   if(n<period+2) { for(int i=0;i<n;i++) out[i]=0.0; return; }
   double plusDM[],minusDM[],tr[];
   ArrayResize(plusDM,n); ArrayResize(minusDM,n);
   plusDM[0]=0; minusDM[0]=0;
   for(int i=1;i<n;i++)
   {
      double up=high[i]-high[i-1];
      double dn=low[i-1]-low[i];
      plusDM[i]=(up>dn && up>0) ? up : 0.0;
      minusDM[i]=(dn>up && dn>0) ? dn : 0.0;
   }
   FE_TrueRange(high,low,close,tr);
   double atrw[];
   FE_Ewm(tr,period,atrw);
   for(int i=0;i<n;i++) if(atrw[i]<1e-12) atrw[i]=1e-12;
   double pdi[],mdi[];
   FE_Ewm(plusDM,period,pdi);
   FE_Ewm(minusDM,period,mdi);
   double dx[];
   ArrayResize(dx,n);
   for(int i=0;i<n;i++)
   {
      pdi[i]=100.0*pdi[i]/atrw[i];
      mdi[i]=100.0*mdi[i]/atrw[i];
      double s=pdi[i]+mdi[i];
      dx[i]=s>1e-12 ? 100.0*MathAbs(pdi[i]-mdi[i])/s : 0.0;
   }
   FE_Ewm(dx,period,out);
}

void FE_EfficiencyRatio(const double &close[], int period, double &out[])
{
   int n=ArraySize(close);
   ArrayResize(out,n);
   for(int i=period;i<n;i++)
   {
      double direction=MathAbs(close[i]-close[i-period]);
      double vol=0.0;
      for(int j=i-period;j<i;j++) vol+=MathAbs(close[j+1]-close[j]);
      out[i]=vol>0 ? direction/vol : 0.0;
   }
   for(int i=0;i<period;i++) out[i]=out[period];
}

void FE_Rsi(const double &close[], int period, double &out[])
{
   int n=ArraySize(close);
   ArrayResize(out,n);
   if(n<period+1) { for(int i=0;i<n;i++) out[i]=50.0; return; }
   double gain[],loss[];
   ArrayResize(gain,n); ArrayResize(loss,n);
   gain[0]=0; loss[0]=0;
   for(int i=1;i<n;i++)
   {
      double d=close[i]-close[i-1];
      gain[i]=d>0 ? d : 0.0;
      loss[i]=d<0 ? -d : 0.0;
   }
   double ag[],al[];
   ArrayResize(ag,n); ArrayResize(al,n);
   double sg=0,sl=0;
   for(int i=1;i<=period;i++) { sg+=gain[i]; sl+=loss[i]; }
   ag[period]=sg/period; al[period]=sl/period;
   for(int i=period+1;i<n;i++)
   {
      ag[i]=(ag[i-1]*(period-1)+gain[i])/period;
      al[i]=(al[i-1]*(period-1)+loss[i])/period;
   }
   for(int i=0;i<n;i++) out[i]=50.0;
   for(int i=period;i<n;i++)
   {
      if(al[i]<1e-12) out[i]=100.0;
      else out[i]=100.0-100.0/(1.0+ag[i]/al[i]);
   }
}

double FE_PercentileRank(const double &series[], double value)
{
   int cnt=0,below=0;
   int n=ArraySize(series);
   for(int i=0;i<n;i++) if(series[i]>0) { cnt++; if(series[i]<value) below++; }
   if(cnt==0) return 0.5;
   return (double)below/cnt;
}

bool FE_RecentCross(const double &fast[], const double &slow[], int last, int lookback=5)
{
   for(int i=MathMax(1,last-lookback);i<=last;i++)
   {
      bool a=fast[i-1]<=slow[i-1];
      bool b=fast[i]<=slow[i];
      if(a!=b) return true;
   }
   return false;
}

//--- Hitung fitur di bar terakhir (index terakhir) dari array kronologis.
//    Kembalikan false bila data kurang dari 60 bar.
bool FE_Compute(const double &open[], const double &high[], const double &low[],
                const double &close[], const double &volume[],
                int atrPeriod,int adxPeriod,int volPeriod,int emaFast,int emaSlow,
                int rsiPeriod,int erPeriod,
                FeatureSnapshot &f,
                int swingN=20,int breakoutLookback=3,double emaDistPct=0.15)
{
   int n=ArraySize(close);
   if(n<60) return false;
   int last=n-1;

   double a[],dx[],er[],r[],ef[],es[],ap[];
   FE_Atr(high,low,close,atrPeriod,a);
   FE_Adx(high,low,close,adxPeriod,dx);
   FE_EfficiencyRatio(close,erPeriod,er);
   FE_Rsi(close,rsiPeriod,r);
   FE_Ema(close,emaFast,ef);
   FE_Ema(close,emaSlow,es);
   ArrayResize(ap,n);
   for(int i=0;i<n;i++) ap[i]=a[i]/MathMax(close[i],1e-12)*100.0;

   f.close=close[last];
   f.atr_pct=ap[last];
   f.atr_pct_percentile=FE_PercentileRank(ap,ap[last]);
   f.adx=dx[last];
   f.er=er[last];
   f.rsi=r[last];
   f.ema_fast=ef[last];
   f.ema_slow=es[last];
   f.ema_bull=ef[last]>es[last];
   f.ema_cross_recent=FE_RecentCross(ef,es,last);

   // volume ratio
   int w=MathMin(volPeriod,last);
   double avg=0.0;
   for(int i=last-w;i<last;i++) avg+=volume[i];
   if(w>0) avg/=w;
   f.vol_ratio=avg>0 ? volume[last]/avg : 1.0;

   f.range_pct=(high[last]-low[last])/MathMax(close[last],1e-12)*100.0;
   f.last_ret_pct=last>0 ? (close[last]/close[last-1]-1.0)*100.0 : 0.0;

   //--- entry context (parity python/features.py)
   int swN=MathMax(swingN,1);
   int sLo=MathMax(0,last-swN+1);
   double swingH=high[sLo], swingL=low[sLo];
   for(int j=sLo+1;j<=last;j++) { swingH=MathMax(swingH,high[j]); swingL=MathMin(swingL,low[j]); }
   f.swing_high=swingH;
   f.swing_low=swingL;
   f.swing_mid=(swingH+swingL)/2.0;
   f.close_vs_ema_slow=MathAbs(es[last])>1e-12 ? (close[last]/es[last]-1.0)*100.0 : 0.0;
   f.near_ema_fast=MathAbs(close[last]-ef[last])/MathMax(ef[last],1e-12)*100.0<=emaDistPct;
   int lb=MathMax(breakoutLookback,1);
   int bLo=MathMax(0,last-lb);
   double bh=high[bLo], bl=low[bLo];
   for(int j=bLo+1;j<last;j++) { bh=MathMax(bh,high[j]); bl=MathMin(bl,low[j]); }
   f.breakout_high=(bLo<last)? close[last]>bh : false;
   f.breakout_low =(bLo<last)? close[last]<bl : false;
   f.last_bar_down=last>0 ? close[last]<close[last-1] : false;
   f.last_bar_up  =last>0 ? close[last]>close[last-1] : false;
   return true;
}
