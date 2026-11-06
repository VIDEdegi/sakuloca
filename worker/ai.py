"""The UI/database know only this contract, not LiteRT-LM internals."""
from typing import Protocol
from pathlib import Path
import hashlib, io, math

PREPROCESS='pillow-exif-rgb-fit1024-jpeg95-v1'
QUERY_PREFIX='task: search query | text: '
AUDIO_QUERY_PREFIX='task: search result | query: '

class EmbeddingBackend(Protocol):
    dimensions: int
    def text(self,text:str)->list[float]: ...
    def image(self,data:bytes)->list[float]: ...
    def close(self)->None: ...

class AudioEmbeddingBackend(Protocol):
    dimensions: int
    def text(self,text:str)->list[float]: ...
    def audio(self,data:bytes)->list[float]: ...
    def close(self)->None: ...

def digest(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def prepare_image(path:Path,edge=1024)->bytes:
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        im=ImageOps.exif_transpose(source).convert('RGB')
        im.thumbnail((edge,edge))
        out=io.BytesIO(); im.save(out,format='JPEG',quality=95)
        return out.getvalue()

def prepare_index_image(path:Path)->tuple[bytes,bytes]:
    """Decode once, retaining the established EXIF/RGB/resize/JPEG pipeline."""
    from PIL import Image, ImageOps
    with Image.open(path) as source:
        rgb=ImageOps.exif_transpose(source).convert('RGB')
        outputs=[]
        for edge in (1024,320):
            with rgb.copy() as image:
                image.thumbnail((edge,edge))
                out=io.BytesIO();image.save(out,format='JPEG',quality=95)
                outputs.append(out.getvalue())
        rgb.close()
        return tuple(outputs)

class LiteRtBackend:
    def __init__(self,model:Path,backend:str,cache:Path):
        from litert_lm import Backend, EmbeddingEngine, EmbeddingOptions, Content
        selected=Backend.GPU() if backend=='gpu' else Backend.CPU(thread_count=4)
        self.engine=EmbeddingEngine(str(model),backend=selected,vision_backend=selected,cache_dir=str(cache),min_input_length=1024,max_input_length=1024)
        self.options=EmbeddingOptions(normalize=True)
        self.Content=Content
        self.dimensions=0
    def _vector(self,content):
        vector=list(self.engine.compute_embedding(content,options=self.options).embedding)
        if not vector or not all(math.isfinite(v) for v in vector): raise ValueError('AI出力が不正です')
        norm=math.sqrt(sum(v*v for v in vector))
        if norm<1e-8: raise ValueError('AI出力がゼロです')
        vector=[v/norm for v in vector]
        if self.dimensions and len(vector)!=self.dimensions: raise ValueError('埋め込み次元が変化しました')
        self.dimensions=len(vector)
        return vector
    def text(self,text): return self._vector(QUERY_PREFIX+text.strip())
    def image(self,data): return self._vector(self.Content.ImageBytes(data))
    def close(self): self.engine.close()

class LiteRtAudioBackend(LiteRtBackend):
    """Separate engine/profile; never changes the established photo pipeline."""
    def __init__(self,model:Path,backend:str,cache:Path):
        from litert_lm import Backend, EmbeddingEngine, EmbeddingOptions, InputOverflowStrategy, Content
        selected=Backend.GPU() if backend=='gpu' else Backend.CPU(thread_count=4)
        self.engine=EmbeddingEngine(str(model),backend=selected,audio_backend=selected,
                                    cache_dir=str(cache),min_input_length=1024,max_input_length=1024)
        self.options=EmbeddingOptions(normalize=True,input_overflow_strategy=InputOverflowStrategy.ERROR)
        self.Content=Content
        self.dimensions=0
    def audio(self,data): return self._vector(self.Content.AudioBytes(data))
    def text(self,text): return self._vector(AUDIO_QUERY_PREFIX+text.strip())
