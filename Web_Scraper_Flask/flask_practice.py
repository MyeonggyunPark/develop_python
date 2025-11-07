from flask import Flask, render_template, request

app = Flask("JobScrapper", template_folder="templates")

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/search")
def search():
    keyword = request.args.get("keyword")
    return render_template("search.html", keyword=keyword)


app.run(debug=True)
