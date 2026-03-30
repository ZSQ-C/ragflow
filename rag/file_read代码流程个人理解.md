```
__init__：初始化该类，需要配置参数读取路径
```

```
read_files：读取指定扩展名的文件，输入参数{文件扩展名列表，是否递归目录（默认true）}
1.supported_extensions：默认支持的扩展名列表，其中k值是文件名格式，v值是对于文件的读取方法
2.根据递归参数值进行是否递归读取判断，通过值执行不同的方法获取文件数据并返回
3.若递归读取执行_read_files_recursive方法，若不递归读取执行_process_files_in_dir方法
```

```
_read_files_recursive：递归读取目录中所有文件，输入参数：{文件路径，指定扩展名，默认扩展名}
1.通过os系统方法对路径进行遍历，如果是目录开始递归执行该方法遍历所有文件
2.如果是文件先判断是否是指定文件格式，再获取它相对总文件的相对路径（方便识别文件位置）和文件后缀，根据后缀通过supported_extensions实现对应的文件读取方法获取数据，将结果以ky键保存
```

```
_process_files_in_dir：处理指定目录中的文件（不递归）
1.递归文件列表将文件一一处理
2.先获取文件后缀格式判断是否是指定处理的文件格式，将文件名和总路径拼接成对应文件的路径，通过后缀查找supported_extensions中对应的读取文件方法，最后以文件名-文件内容kv键保存
```

```
_read_txt,_read_pdf,_read_markdown,_read_docx,_read_doc,_read_csv,_read_json,
_read_yaml,接下来就是实现supported_extensions字典中所有处理文件格式的方法

1._read_txt：通过编码处理模块codecs（核心作用是解决文件读取 / 写入时的编码兼容问题，而非 “替	代文件读取”）处理不同编码文件，相比open（）方法遇到不同编码文件更健壮先使用 encoding='utf-	  8'编码读取文件，若是报错则尝试用其它编码方式，此时需要通过chardet模块检测该文件是属于哪种编    码，再通过codecs指定该编码读取文件

2._read_pdf：通过PyPDF2.PdfReader方法读取pdf获取其中的数据，然后再获取pdf的页数，通过pages方法读取指定页面的数据并提取其中的文本数据，将每页的文本数据拼接到str数据中（注意异常处理）

3._read_markdown：直接用codecs方法通过encoding='utf-8'编码读取文本数据
   有个疑惑：为什么这个文件格式不用像text一样做编码异常处理呢？
	
4._read_docx：通过Document方法读取word文档（- doc.paragraphs ：文档中的所有段落（最常	   	  用）- doc.tables ：文档中的所有表格- doc.sections ：文档的节（页面设置、页眉页脚等）-   	 doc.styles ：文档的样式集合）

5._read_doc：读取旧版本的word文档，方法多不详讲

6._read_csv：通过csv.reader读取CSV文件数据获取每行文本的列汇总，对列进行遍历获取文本添加到总	 文本中，这里也需要考虑编码问题，若编码失败也需要先获取其对应的编码方式再通过open（）方法指定	编码获取数据  有个疑惑：咋判断是否需要编码异常处理，不同的文件格式读取的方法都不太一样需要总结

7.read_csv_as_dicts：这里需要返回dict格式，故通过csv.DictReader方法获取字典数据（行，*）
	疑惑：为什么同一个文档处理，这里又没有针对编码问题额外处理

8._read_json：通过json.load读取json数据，json.dumps返回文本数据（理解一下）
9.read_json_as_dict：通过json.load读取json数据并直接返回不用dunps处理

10._read_yaml：通过yaml.load读取并dunps返回文本数据，和上面的json差不多
```

```
list_all_files：列出目录中的所有文件（子目录中的以相对路径返回），也是通过参数recursive决定是否递交找所有文件
1.递交-通过os.walk返回一个三元组：- root ：当前遍历到的目录路径- dirs ：当前目录下的子目录列表- filenames ：当前目录下的文件列表
2.遍历filenames并通过os.path.relpath获取每个文件相对路径并返回
3.非递交：直接通过os.listdir()方法返回所有文件
```

