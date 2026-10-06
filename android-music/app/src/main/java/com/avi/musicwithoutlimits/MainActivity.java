package il.nolimits.music;

import android.app.*;
import android.os.*;
import android.content.*;
import android.graphics.Typeface;
import android.media.AudioAttributes;
import android.media.MediaPlayer;
import android.net.Uri;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.util.*;

public class MainActivity extends Activity {
    static class Track {
        String title, artist, url;
        boolean favorite;
        Track(String t, String a, String u) { title=t; artist=a; url=u; }
        JSONObject json() throws JSONException {
            JSONObject o=new JSONObject(); o.put("title",title); o.put("artist",artist); o.put("url",url); o.put("favorite",favorite); return o;
        }
        static Track from(JSONObject o) {
            Track t=new Track(o.optString("title","שיר"),o.optString("artist",""),o.optString("url",""));
            t.favorite=o.optBoolean("favorite",false); return t;
        }
    }
    static class Playlist {
        String name, description="";
        boolean isPublic=false;
        ArrayList<Track> tracks=new ArrayList<>();
        Playlist(String n){name=n;}
        JSONObject json() throws JSONException {
            JSONObject o=new JSONObject(); o.put("name",name); o.put("description",description); o.put("public",isPublic);
            JSONArray a=new JSONArray(); for(Track t:tracks)a.put(t.json()); o.put("tracks",a); return o;
        }
        static Playlist from(JSONObject o){
            Playlist p=new Playlist(o.optString("name","פלייליסט")); p.description=o.optString("description","");
            p.isPublic=o.optBoolean("public",false); JSONArray a=o.optJSONArray("tracks");
            if(a!=null) for(int i=0;i<a.length();i++) p.tracks.add(Track.from(a.optJSONObject(i)));
            return p;
        }
    }

    SharedPreferences prefs;
    ArrayList<Playlist> playlists=new ArrayList<>();
    int playlistIndex=0, trackIndex=-1;
    boolean shuffle=false;
    int repeatMode=0;
    MediaPlayer player;
    LinearLayout root, trackList;
    TextView now;
    Spinner playlistSpinner;
    Button shuffleBtn, repeatBtn;

    @Override public void onCreate(Bundle b){
        super.onCreate(b);
        getWindow().getDecorView().setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        prefs=getSharedPreferences("music_without_limits",MODE_PRIVATE);
        load();
        buildUi();
        maybeShowIntro();
        restorePlaybackState();
    }

    void buildUi(){
        ScrollView scroll=new ScrollView(this);
        root=new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL); root.setPadding(24,24,24,60);
        root.setLayoutDirection(View.LAYOUT_DIRECTION_RTL); scroll.addView(root);
        TextView title=new TextView(this); title.setText("מוזיקה ללא גבולות"); title.setTextSize(28); title.setTypeface(null,Typeface.BOLD);
        root.addView(title);

        playlistSpinner=new Spinner(this); refreshSpinner(); root.addView(playlistSpinner);
        playlistSpinner.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener(){
            public void onItemSelected(android.widget.AdapterView<?> p,View v,int pos,long id){ playlistIndex=pos; save(); renderTracks(); }
            public void onNothingSelected(android.widget.AdapterView<?> p){}
        });

        LinearLayout pbar=row();
        pbar.addView(btn("חדש",v->newPlaylist()));
        pbar.addView(btn("ערוך",v->editPlaylist()));
        pbar.addView(btn("מחק",v->deletePlaylist()));
        pbar.addView(btn("שתף",v->sharePlaylist()));
        root.addView(pbar);

        LinearLayout controls=row();
        controls.addView(btn("⏮",v->previous()));
        controls.addView(btn("▶/⏸",v->togglePlay()));
        controls.addView(btn("⏭",v->next()));
        shuffleBtn=btn("ערבוב: כבוי",v->{shuffle=!shuffle; updateControlLabels(); save();});
        controls.addView(shuffleBtn);
        root.addView(controls);

        LinearLayout controls2=row();
        repeatBtn=btn("חזרה: כבוי",v->{repeatMode=(repeatMode+1)%3; updateControlLabels(); save();});
        controls2.addView(repeatBtn);
        controls2.addView(btn("מיון",v->showSortMenu(v)));
        controls2.addView(btn("+ שיר",v->addTrack()));
        controls2.addView(btn("מועדפים",v->showFavorites()));
        root.addView(controls2);

        now=new TextView(this); now.setText("לא מתנגן שיר"); now.setTextSize(17); now.setPadding(0,18,0,18); root.addView(now);
        trackList=new LinearLayout(this); trackList.setOrientation(LinearLayout.VERTICAL); root.addView(trackList);
        updateControlLabels(); renderTracks();
        setContentView(scroll);
    }

    LinearLayout row(){
        LinearLayout r=new LinearLayout(this); r.setOrientation(LinearLayout.HORIZONTAL); r.setGravity(Gravity.RIGHT);
        return r;
    }
    Button btn(String s,View.OnClickListener l){
        Button b=new Button(this); b.setText(s); b.setOnClickListener(l);
        b.setAllCaps(false); b.setMinHeight(0); b.setMinWidth(0);
        b.setLayoutParams(new LinearLayout.LayoutParams(0,LinearLayout.LayoutParams.WRAP_CONTENT,1));
        return b;
    }

    Playlist current(){ if(playlists.isEmpty())playlists.add(new Playlist("הפלייליסט שלי")); playlistIndex=Math.max(0,Math.min(playlistIndex,playlists.size()-1)); return playlists.get(playlistIndex); }
    void refreshSpinner(){
        ArrayList<String> names=new ArrayList<>(); for(Playlist p:playlists) names.add((p.isPublic?"🌐 ":"🔒 ")+p.name);
        ArrayAdapter<String> a=new ArrayAdapter<>(this,android.R.layout.simple_spinner_dropdown_item,names);
        playlistSpinner.setAdapter(a); if(playlistIndex<names.size()) playlistSpinner.setSelection(playlistIndex);
    }

    void renderTracks(){
        if(trackList==null)return; trackList.removeAllViews();
        Playlist p=current();
        TextView meta=new TextView(this); meta.setText(p.tracks.size()+" שירים · "+(p.isPublic?"ציבורי":"פרטי")); trackList.addView(meta);
        for(int i=0;i<p.tracks.size();i++){
            final int idx=i; Track t=p.tracks.get(i);
            LinearLayout box=new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL); box.setPadding(0,12,0,12);
            TextView n=new TextView(this); n.setText((i+1)+". "+(t.favorite?"★ ":"")+t.title+(t.artist.isEmpty()?"":" — "+t.artist)); n.setTextSize(18); box.addView(n);
            LinearLayout actions=row();
            actions.addView(btn("נגן",v->play(idx,0)));
            actions.addView(btn("↑",v->move(idx,-1)));
            actions.addView(btn("↓",v->move(idx,1)));
            actions.addView(btn(t.favorite?"★":"☆",v->{t.favorite=!t.favorite;save();renderTracks();}));
            actions.addView(btn("שתף",v->shareTrack(t)));
            actions.addView(btn("✕",v->removeTrack(idx)));
            box.addView(actions); trackList.addView(box);
        }
    }

    void newPlaylist(){
        final EditText e=new EditText(this); e.setHint("שם הפלייליסט");
        new AlertDialog.Builder(this).setTitle("פלייליסט חדש").setView(e).setPositiveButton("צור",(d,w)->{
            String n=e.getText().toString().trim(); if(n.isEmpty())n="פלייליסט חדש";
            playlists.add(new Playlist(n)); playlistIndex=playlists.size()-1; save(); refreshSpinner(); renderTracks();
        }).setNegativeButton("ביטול",null).show();
    }
    void editPlaylist(){
        Playlist p=current();
        LinearLayout l=new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL);
        EditText name=new EditText(this); name.setText(p.name); name.setHint("שם"); l.addView(name);
        EditText desc=new EditText(this); desc.setText(p.description); desc.setHint("תיאור"); l.addView(desc);
        CheckBox pub=new CheckBox(this); pub.setText("פלייליסט ציבורי"); pub.setChecked(p.isPublic); l.addView(pub);
        new AlertDialog.Builder(this).setTitle("עריכת פלייליסט").setView(l).setPositiveButton("שמור",(d,w)->{
            p.name=name.getText().toString().trim(); if(p.name.isEmpty())p.name="פלייליסט";
            p.description=desc.getText().toString(); p.isPublic=pub.isChecked(); save(); refreshSpinner(); renderTracks();
        }).setNegativeButton("ביטול",null).show();
    }
    void deletePlaylist(){
        if(playlists.size()==1){toast("חייב להישאר לפחות פלייליסט אחד");return;}
        new AlertDialog.Builder(this).setTitle("מחיקת פלייליסט").setMessage("למחוק את "+current().name+"?")
            .setPositiveButton("מחק",(d,w)->{playlists.remove(playlistIndex);playlistIndex=Math.max(0,playlistIndex-1);save();refreshSpinner();renderTracks();})
            .setNegativeButton("ביטול",null).show();
    }

    void addTrack(){
        LinearLayout l=new LinearLayout(this); l.setOrientation(LinearLayout.VERTICAL);
        EditText title=new EditText(this); title.setHint("שם השיר"); l.addView(title);
        EditText artist=new EditText(this); artist.setHint("אמן"); l.addView(artist);
        EditText url=new EditText(this); url.setHint("קישור ישיר לקובץ שמע"); l.addView(url);
        new AlertDialog.Builder(this).setTitle("הוסף שיר").setView(l).setPositiveButton("הוסף",(d,w)->{
            String u=url.getText().toString().trim(); if(u.isEmpty()){toast("צריך קישור לשיר");return;}
            String t=title.getText().toString().trim(); if(t.isEmpty())t="שיר חדש";
            current().tracks.add(new Track(t,artist.getText().toString().trim(),u)); save(); renderTracks();
        }).setNegativeButton("ביטול",null).show();
    }

    void removeTrack(int i){ if(i>=0&&i<current().tracks.size()){current().tracks.remove(i); if(trackIndex==i)stopPlayer(); save();renderTracks();}}
    void move(int i,int d){ int j=i+d; if(i<0||j<0||j>=current().tracks.size())return; Collections.swap(current().tracks,i,j); save(); renderTracks(); }

    void play(int idx,int seekMs){
        Playlist p=current(); if(idx<0||idx>=p.tracks.size())return; trackIndex=idx; Track t=p.tracks.get(idx);
        stopPlayer(); player=new MediaPlayer();
        player.setAudioAttributes(new AudioAttributes.Builder().setContentType(AudioAttributes.CONTENT_TYPE_MUSIC).setUsage(AudioAttributes.USAGE_MEDIA).build());
        try{
            player.setDataSource(t.url); now.setText("טוען: "+t.title);
            player.setOnPreparedListener(mp->{ if(seekMs>0)mp.seekTo(seekMs); mp.start(); now.setText("מתנגן: "+t.title+(t.artist.isEmpty()?"":" — "+t.artist)); savePlaybackState();});
            player.setOnCompletionListener(mp->{ if(repeatMode==2)play(trackIndex,0); else next();});
            player.setOnErrorListener((mp,what,extra)->{toast("לא הצלחתי לנגן את הקישור");return true;});
            player.prepareAsync();
        }catch(Exception e){toast("שגיאת ניגון: "+e.getMessage());}
    }
    void togglePlay(){
        if(player!=null){ if(player.isPlaying()){player.pause();now.setText("מושהה");savePlaybackState();} else {player.start();now.setText("ממשיך לנגן");} }
        else if(!current().tracks.isEmpty()) play(trackIndex>=0?trackIndex:0,0);
    }
    void next(){
        int n=current().tracks.size(); if(n==0)return;
        if(shuffle && n>1){ int x=new Random().nextInt(n); if(x==trackIndex)x=(x+1)%n; play(x,0); return; }
        int ni=trackIndex+1; if(ni>=n){ if(repeatMode==1)ni=0; else {stopPlayer();now.setText("סוף הפלייליסט");return;} } play(ni,0);
    }
    void previous(){ int n=current().tracks.size(); if(n==0)return; int ni=trackIndex-1; if(ni<0)ni=(repeatMode==1?n-1:0); play(ni,0); }
    void stopPlayer(){ if(player!=null){try{player.stop();}catch(Exception ignored){} player.release(); player=null;} }
    void updateControlLabels(){
        if(shuffleBtn!=null)shuffleBtn.setText("ערבוב: "+(shuffle?"פעיל":"כבוי"));
        if(repeatBtn!=null)repeatBtn.setText("חזרה: "+(repeatMode==0?"כבוי":repeatMode==1?"פלייליסט":"שיר"));
    }

    void showSortMenu(View anchor){
        PopupMenu m=new PopupMenu(this,anchor);
        m.getMenu().add("שם"); m.getMenu().add("אמן"); m.getMenu().add("מועדפים קודם");
        m.setOnMenuItemClickListener(item->{String s=item.getTitle().toString();
            if(s.equals("שם")) Collections.sort(current().tracks,(a,b)->a.title.compareToIgnoreCase(b.title));
            else if(s.equals("אמן")) Collections.sort(current().tracks,(a,b)->a.artist.compareToIgnoreCase(b.artist));
            else Collections.sort(current().tracks,(a,b)->Boolean.compare(b.favorite,a.favorite));
            save();renderTracks();return true;}); m.show();
    }
    void showFavorites(){
        int c=0; StringBuilder s=new StringBuilder(); for(Track t:current().tracks) if(t.favorite){c++;s.append("★ ").append(t.title).append("\n");}
        new AlertDialog.Builder(this).setTitle("מועדפים ("+c+")").setMessage(c==0?"אין עדיין מועדפים":s.toString()).setPositiveButton("סגור",null).show();
    }
    void shareTrack(Track t){
        Intent i=new Intent(Intent.ACTION_SEND); i.setType("text/plain"); i.putExtra(Intent.EXTRA_TEXT,t.title+(t.artist.isEmpty()?"":" — "+t.artist)+"\n"+t.url); startActivity(Intent.createChooser(i,"שתף שיר"));
    }
    void sharePlaylist(){
        Playlist p=current(); StringBuilder s=new StringBuilder(p.name+"\n");
        for(Track t:p.tracks)s.append("• ").append(t.title).append(t.artist.isEmpty()?"":" — "+t.artist).append("\n").append(t.url).append("\n");
        Intent i=new Intent(Intent.ACTION_SEND); i.setType("text/plain"); i.putExtra(Intent.EXTRA_TEXT,s.toString()); startActivity(Intent.createChooser(i,"שתף פלייליסט"));
    }

    void maybeShowIntro(){
        if(!prefs.getBoolean("show_intro",true))return;
        CheckBox never=new CheckBox(this); never.setText("אל תנגן את הקליפ הזה שוב בפתיחת האפליקציה");
        new AlertDialog.Builder(this).setTitle("מוזיקה ללא גבולות").setMessage("קליפ הפתיחה")
            .setView(never)
            .setPositiveButton("נגן קליפ",(d,w)->{ if(never.isChecked())prefs.edit().putBoolean("show_intro",false).apply(); startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://youtu.be/nRfo1NdTN7k")));})
            .setNegativeButton("דלג",(d,w)->{if(never.isChecked())prefs.edit().putBoolean("show_intro",false).apply();}).show();
    }

    void save(){
        try{
            JSONArray a=new JSONArray(); for(Playlist p:playlists)a.put(p.json());
            prefs.edit().putString("playlists",a.toString()).putInt("playlist_index",playlistIndex).putBoolean("shuffle",shuffle).putInt("repeat",repeatMode).apply();
        }catch(Exception ignored){}
    }
    void load(){
        shuffle=prefs.getBoolean("shuffle",false); repeatMode=prefs.getInt("repeat",0); playlistIndex=prefs.getInt("playlist_index",0);
        String raw=prefs.getString("playlists","");
        if(!raw.isEmpty()) try{JSONArray a=new JSONArray(raw); for(int i=0;i<a.length();i++)playlists.add(Playlist.from(a.getJSONObject(i)));}catch(Exception ignored){}
        if(playlists.isEmpty())playlists.add(new Playlist("הפלייליסט שלי"));
    }
    void savePlaybackState(){
        int pos=0; try{if(player!=null)pos=player.getCurrentPosition();}catch(Exception ignored){}
        prefs.edit().putInt("last_track",trackIndex).putInt("last_pos",pos).putInt("last_playlist",playlistIndex).apply();
    }
    void restorePlaybackState(){
        int lp=prefs.getInt("last_playlist",-1), lt=prefs.getInt("last_track",-1), pos=prefs.getInt("last_pos",0);
        if(lp>=0&&lp<playlists.size()){playlistIndex=lp;refreshSpinner(); if(lt>=0&&lt<current().tracks.size()){trackIndex=lt;now.setText("המשך זמין: "+current().tracks.get(lt).title+" ("+(pos/1000)+" שנ׳)");}}
    }
    void toast(String s){Toast.makeText(this,s,Toast.LENGTH_LONG).show();}
    @Override protected void onPause(){super.onPause();savePlaybackState();}
    @Override protected void onDestroy(){super.onDestroy();stopPlayer();}
}
