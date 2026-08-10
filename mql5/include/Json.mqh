//+------------------------------------------------------------------+
//| Json.mqh - minimal JSON parser untuk MQL5                         |
//| Mendukung: object, array, string, number, bool, null.             |
//| Dipakai untuk membaca respons bridge (LLM decision).              |
//+------------------------------------------------------------------+
#property strict

enum JsonType { J_NULL=0, J_BOOL, J_NUMBER, J_STRING, J_ARRAY, J_OBJECT };

class CJsonValue
{
public:
   JsonType   m_type;
   double     m_number;
   string     m_string;
   bool       m_bool;
   CJsonValue *m_arr[];
   string     m_obj_keys[];
   CJsonValue *m_obj_vals[];

   CJsonValue()                    { m_type=J_NULL; m_number=0; m_string=""; m_bool=false; }
   ~CJsonValue()
   {
      int i;
      for(i=0;i<ArraySize(m_arr);i++)       if(CheckPointer(m_arr[i])==POINTER_DYNAMIC) delete m_arr[i];
      for(i=0;i<ArraySize(m_obj_vals);i++)  if(CheckPointer(m_obj_vals[i])==POINTER_DYNAMIC) delete m_obj_vals[i];
   }

   int Size() const
   {
      if(m_type==J_ARRAY)  return ArraySize(m_arr);
      if(m_type==J_OBJECT) return ArraySize(m_obj_vals);
      return 0;
   }
   string Key(int i) const { if(m_type==J_OBJECT) return m_obj_keys[i]; return ""; }
   CJsonValue *At(int i) const
   {
      if(m_type==J_ARRAY)  return m_arr[i];
      if(m_type==J_OBJECT) return m_obj_vals[i];
      return NULL;
   }
   CJsonValue *Get(string key) const
   {
      if(m_type!=J_OBJECT) return NULL;
      int i;
      for(i=0;i<ArraySize(m_obj_keys);i++)
         if(m_obj_keys[i]==key) return m_obj_vals[i];
      return NULL;
   }
   string AsString() const
   {
      if(m_type==J_STRING) return m_string;
      if(m_type==J_NUMBER) return DoubleToString(m_number,8);
      return "";
   }
   double AsDouble() const
   {
      if(m_type==J_NUMBER) return m_number;
      if(m_type==J_STRING) return StringToDouble(m_string);
      if(m_type==J_BOOL)   return m_bool ? 1.0 : 0.0;
      return 0.0;
   }
   bool AsBool() const { if(m_type==J_BOOL) return m_bool; return false; }
};

class CJsonParser
{
private:
   string m_src;
   int    m_pos;

   void SkipWs()
   {
      while(m_pos<StringLen(m_src) && StringGetCharacter(m_src,m_pos)==' ') m_pos++;
   }

   CJsonValue *ParseObject()
   {
      CJsonValue *o=new CJsonValue();
      if(o==NULL) return NULL;
      o.m_type=J_OBJECT;
      m_pos++;                       // '{'
      SkipWs();
      if(m_pos<StringLen(m_src) && StringGetCharacter(m_src,m_pos)=='}') { m_pos++; return o; }
      while(true)
      {
         SkipWs();
         CJsonValue *k=ParseString();
         SkipWs();
         if(m_pos<StringLen(m_src) && StringGetCharacter(m_src,m_pos)==':') m_pos++;
         SkipWs();
         CJsonValue *val=ParseValue();
         if(k!=NULL && val!=NULL)
         {
            int sz=ArraySize(o.m_obj_keys);
            ArrayResize(o.m_obj_keys,sz+1);
            ArrayResize(o.m_obj_vals,sz+1);
            o.m_obj_keys[sz]=k.m_string;
            o.m_obj_vals[sz]=val;
            delete k;
         }
         else
         {
            if(k!=NULL)   delete k;
            if(val!=NULL) delete val;
         }
         SkipWs();
         if(m_pos>=StringLen(m_src)) break;
         int ch=StringGetCharacter(m_src,m_pos);
         if(ch==',') { m_pos++; continue; }
         if(ch=='}') { m_pos++; break; }
         break;
      }
      return o;
   }

   CJsonValue *ParseArray()
   {
      CJsonValue *a=new CJsonValue();
      if(a==NULL) return NULL;
      a.m_type=J_ARRAY;
      m_pos++;                       // '['
      SkipWs();
      if(m_pos<StringLen(m_src) && StringGetCharacter(m_src,m_pos)==']') { m_pos++; return a; }
      while(true)
      {
         SkipWs();
         CJsonValue *val=ParseValue();
         if(val!=NULL)
         {
            int sz=ArraySize(a.m_arr);
            ArrayResize(a.m_arr,sz+1);
            a.m_arr[sz]=val;
         }
         SkipWs();
         if(m_pos>=StringLen(m_src)) break;
         int ch=StringGetCharacter(m_src,m_pos);
         if(ch==',') { m_pos++; continue; }
         if(ch==']') { m_pos++; break; }
         break;
      }
      return a;
   }

   CJsonValue *ParseString()
   {
      CJsonValue *s=new CJsonValue();
      if(s==NULL) return NULL;
      s.m_type=J_STRING;
      m_pos++;                       // opening quote
      string buf="";
      while(m_pos<StringLen(m_src))
      {
         int ch=StringGetCharacter(m_src,m_pos);
         if(ch=='\\')
         {
            m_pos++;
            if(m_pos<StringLen(m_src))
            {
               int e=StringGetCharacter(m_src,m_pos);
               if(e=='n') buf+="\n";
               else if(e=='t') buf+="\t";
               else buf+=ShortToString((ushort)e);
            }
            m_pos++;
            continue;
         }
         if(ch=='"') { m_pos++; break; }
         buf+=ShortToString((ushort)ch);
         m_pos++;
      }
      s.m_string=buf;
      return s;
   }

   CJsonValue *ParseBool()
   {
      CJsonValue *b=new CJsonValue();
      if(b==NULL) return NULL;
      b.m_type=J_BOOL;
      if(StringSubstr(m_src,m_pos,4)=="true")      { b.m_bool=true;  m_pos+=4; }
      else if(StringSubstr(m_src,m_pos,5)=="false"){ b.m_bool=false; m_pos+=5; }
      return b;
   }

   CJsonValue *ParseNumber()
   {
      CJsonValue *n=new CJsonValue();
      if(n==NULL) return NULL;
      n.m_type=J_NUMBER;
      int start=m_pos;
      while(m_pos<StringLen(m_src))
      {
         int ch=StringGetCharacter(m_src,m_pos);
         if((ch>='0'&&ch<='9')||ch=='-'||ch=='+'||ch=='.'||ch=='e'||ch=='E') m_pos++;
         else break;
      }
      n.m_number=StringToDouble(StringSubstr(m_src,start,m_pos-start));
      return n;
   }

   CJsonValue *ParseValue()
   {
      SkipWs();
      if(m_pos>=StringLen(m_src)) return NULL;
      int ch=StringGetCharacter(m_src,m_pos);
      if(ch=='{') return ParseObject();
      if(ch=='[') return ParseArray();
      if(ch=='"') return ParseString();
      if(ch=='t'||ch=='f') return ParseBool();
      if(ch=='n') { m_pos+=4; return new CJsonValue(); }   // null
      return ParseNumber();
   }

public:
   //--- Parse seluruh string, kembalikan root (harus di-delete caller)
   CJsonValue *Parse(string src)
   {
      m_src=src;
      m_pos=0;
      CJsonValue *v=ParseValue();
      return v;
   }
};
