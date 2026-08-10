//+------------------------------------------------------------------+
//| Bridge.mqh - client WebRequest ke Python bridge lokal             |
//| Mengirim bars OHLCV multi-TF + posisi + akun, membaca keputusan.  |
//| CATATAN: URL http://127.0.0.1:8080 harus di-whitelist di          |
//| MT5 -> Tools -> Options -> Expert Advisors -> Allow WebRequest.   |
//+------------------------------------------------------------------+
#property strict

//--- hasil keputusan dari bridge (parsed)
struct BridgeDecision
{
   bool   ok;             // false bila network error
   string engine;         // llm | deterministic | error
   string action;         // OPEN CLOSE MODIFY HOLD
   string bias;           // LONG SHORT FLAT
   double entry, sl, tp, lots, confidence;
   string regime_label;
   string reasoning;
   string raw;            // raw JSON respons utk logging
};

class CBridge
{
public:
   //--- serialize MqlRates (kronologis) menjadi bagian JSON array
   static string BarsToJson(string tfName, const MqlRates &rates[])
   {
      int n=ArraySize(rates);
      if(n<=0) return StringFormat("\"%s\":{\"t\":[],\"o\":[],\"h\":[],\"l\":[],\"c\":[],\"v\":[]}",tfName);
      string t="\"t\":[",o="\"o\":[",h="\"h\":[",l="\"l\":[",c="\"c\":[",v="\"v\":[";
      int i;
      for(i=0;i<n;i++)
      {
         t+=IntegerToString((long)rates[i].time)+(i<n-1?",":"");
         o+=DoubleToString(rates[i].open,_Digits)+(i<n-1?",":"");
         h+=DoubleToString(rates[i].high,_Digits)+(i<n-1?",":"");
         l+=DoubleToString(rates[i].low,_Digits)+(i<n-1?",":"");
         c+=DoubleToString(rates[i].close,_Digits)+(i<n-1?",":"");
         v+=DoubleToString(rates[i].tick_volume,0)+(i<n-1?",":"");
      }
      return StringFormat("\"%s\":{%s],%s],%s],%s],%s],%s]}",tfName,t,o,h,l,c,v);
   }

   //--- serialize posisi terbuka simbol ini
   static string PositionsToJson(const string symbol)
   {
      string s="\"positions\":[";
      int total=0;
      for(int i=PositionsTotal()-1;i>=0;i--)
      {
         ulong tk=PositionGetTicket(i);
         if(tk==0) continue;
         if(PositionGetString(POSITION_SYMBOL)!=symbol) continue;
         if(total>0) s+=",";
         int type=(int)PositionGetInteger(POSITION_TYPE);
         double lots=PositionGetDouble(POSITION_VOLUME);
         double op=PositionGetDouble(POSITION_PRICE_OPEN);
         double sl=PositionGetDouble(POSITION_SL);
         double tp=PositionGetDouble(POSITION_TP);
         double pnl=PositionGetDouble(POSITION_PROFIT);
         s+=StringFormat("{\"type\":%d,\"lots\":%.2f,\"open_price\":%s,\"sl\":%s,\"tp\":%s,\"profit\":%.2f}",
                         type,lots,DoubleToString(op,_Digits),DoubleToString(sl,_Digits),
                         DoubleToString(tp,_Digits),pnl);
         total++;
      }
      s+="]";
      return s;
   }

   //--- bangun payload JSON lengkap
   static string BuildRequest(string symbol,string timeframe,
                              const MqlRates &ratesM5[],const MqlRates &ratesM15[],
                              const MqlRates &ratesH1[],const MqlRates &ratesH4[],
                              double balance,double equity,double tickSize,double tickValue,
                              double spread,ulong requestId,string bridgeToken="")
   {
      string s="{";
      s+="\"symbol\":\""+symbol+"\",";
      s+="\"timeframe\":\""+timeframe+"\",";
      s+=StringFormat("\"request_id\":\"%s\",",IntegerToString((long)requestId));
      s+="\"bars\":{";
      s+=BarsToJson("M5",ratesM5)+",";
      s+=BarsToJson("M15",ratesM15)+",";
      s+=BarsToJson("H1",ratesH1)+",";
      s+=BarsToJson("H4",ratesH4);
      s+="},";
      s+=PositionsToJson(symbol)+",";
      s+=StringFormat("\"account\":{\"balance\":%.2f,\"equity\":%.2f,\"tick_size\":%.8f,\"tick_value\":%.6f,\"spread\":%.6f},"
                      ,balance,equity,tickSize,tickValue,spread);
      s+="\"server_time\":"+IntegerToString((long)TimeCurrent());
      if(StringLen(bridgeToken)>0)
      {
         s+=",\"bridge_token\":\""+bridgeToken+"\"";
      }
      s+="}";
      return s;
   }

   //--- kirim POST, parse respons. return false bila network error.
   static bool Send(string url,string payload,int timeoutMs,BridgeDecision &dec)
   {
      dec.ok=false;
      char post[],result[];
      StringToCharArray(payload,post);
      // buang null terminator
      int sz=ArraySize(post);
      if(sz>0 && post[sz-1]==0) sz--;
      ArrayResize(post,sz);

      string resultHeaders;
      ResetLastError();
      // overload 7-arg (tanpa headers request). FastAPI/starlette tetap parse JSON
      // tanpa Content-Type header.
      int res=WebRequest("POST",url,"",timeoutMs,post,result,resultHeaders);
      if(res==-1)
      {
         dec.raw="WebRequest error #"+IntegerToString(GetLastError());
         dec.reasoning=dec.raw;
         return false;
      }
      dec.raw=CharArrayToString(result,0,WHOLE_ARRAY,CP_UTF8);
      dec.ok=true;
      return true;
   }

   //--- parse respons bridge ke BridgeDecision (harus ok==true)
   static void Parse(const string &raw,BridgeDecision &dec)
   {
      dec.raw=raw;
      CJsonParser p;
      CJsonValue *root=p.Parse(raw);
      if(root==NULL) { dec.ok=false; return; }

      CJsonValue *v=root.Get("engine");   dec.engine   = v!=NULL?v.AsString():"";
      v=root.Get("action");               dec.action   = v!=NULL?v.AsString():"HOLD";
      v=root.Get("bias");                 dec.bias     = v!=NULL?v.AsString():"FLAT";
      v=root.Get("entry");                dec.entry    = v!=NULL?v.AsDouble():0.0;
      v=root.Get("sl");                   dec.sl       = v!=NULL?v.AsDouble():0.0;
      v=root.Get("tp");                   dec.tp       = v!=NULL?v.AsDouble():0.0;
      v=root.Get("lot");                  dec.lots     = v!=NULL?v.AsDouble():0.0;
      v=root.Get("confidence");           dec.confidence=v!=NULL?v.AsDouble():0.0;
      v=root.Get("regime_label");         dec.regime_label=v!=NULL?v.AsString():"";
      v=root.Get("reasoning");            dec.reasoning=v!=NULL?v.AsString():"";

      // bila bridge mengembalikan error, action HOLD
      v=root.Get("error");
      if(v!=NULL) { dec.action="HOLD"; dec.engine="error"; dec.reasoning=v.AsString(); }
      delete root;
   }
};
