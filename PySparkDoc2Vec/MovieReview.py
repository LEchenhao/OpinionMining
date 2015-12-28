import re
import sys
import numpy as np
from gensim.models.word2vec import Word2Vec
from gensim.models.doc2vec import TaggedDocument
from DistDoc2VecFast import DistDoc2VecFast
from Utility.Preprocessing import pre_processing

try:
    from pyspark import SparkContext, SparkConf
    from pyspark.mllib.linalg import SparseVector
    from pyspark.mllib.regression import LabeledPoint
    from pyspark.mllib.classification import NaiveBayes
    from pyspark.mllib.classification import SVMWithSGD, SVMModel
    from pyspark.mllib.classification import LogisticRegressionWithLBFGS, LogisticRegressionModel
    from pyspark.mllib.feature import PCA

    print ("Successfully imported PySparkTWIDF Modules")

except ImportError as e:
    print ("Can not import PySparkTWIDF Modules", e)
    sys.exit(1)


def swap_kv(tp):
    return tp[1], tp[0]


def sentences(l):
    l = re.compile(r'([!\?])').sub(r' \1 .', l).rstrip("(\.)*\n")
    return l.split(".")


def parse_sentences(rdd):
    raw = rdd.zipWithIndex().map(swap_kv)

    data = raw.flatMap(lambda (id, text): [(id, pre_processing(s).split()) for s in sentences(text)])
    return data


def parse_paragraphs(rdd):
    raw = rdd.zipWithIndex().map(swap_kv)

    def clean_paragraph(text):
        paragraph = []
        for s in sentences(text):
            paragraph = paragraph + pre_processing(s).split()

        return paragraph

    data = raw.map(lambda (id, text): TaggedDocument(words=clean_paragraph(text), tags=[id]))
    return data


def word2vec(rdd):

    sentences = parse_sentences(rdd)
    sentences_without_id = sentences.map(lambda (id, sent): sent)
    model = Word2Vec(size=100, hs=0, negative=8)

    dd2v = DistDoc2VecFast(model, learn_hidden=True, num_partitions=15, num_iterations=20)
    dd2v.build_vocab_from_rdd(sentences_without_id)

    print "*** done training words ****"
    print "*** len(model.vocab): %d ****" % len(model.vocab)
    return dd2v, sentences


def doc2vec(dd2v, rdd):

    paragraphs = parse_paragraphs(rdd)
    dd2v.train_sentences_cbow(paragraphs)
    print "**** Done Training Doc2Vec ****"

    def split_vec(iterable):
        dvecs = iter(iterable).next()
        dvecs = dvecs['doctag_syn0']
        n = np.shape(dvecs)[0]
        return (dvecs[i] for i in xrange(n))

    return dd2v, dd2v.doctag_syn0.mapPartitions(split_vec)


def regression(reg_data):
    
    train_data, test_data = reg_data.randomSplit([0.7, 0.3])
    model = LogisticRegressionWithLBFGS.train(train_data)
    labels_predictions = test_data.map(lambda p: (p.label, model.predict(p.features)))

    train_error = labels_predictions.filter(lambda (v, p): v != p).count() / float(test_data.count())
    false_pos = labels_predictions.filter(lambda (v, p): v != p and v == 0.0).count() / float(
        test_data.filter(lambda lp: lp.label == 0.0).count())
    false_neg = labels_predictions.filter(lambda (v, p): v != p and v == 1.0).count() / float(
        test_data.filter(lambda lp: lp.label == 1.0).count())

    print "*** Error Rate: %f ***" % train_error
    print "*** False Positive Rate: %f ***" % false_pos
    print "*** False Negative Rate: %f ***" % false_neg


if __name__ == "__main__":

    conf = (SparkConf()
            .set("spark.driver.maxResultSize", "4g"))

    sc = SparkContext(conf=conf,
                      pyFiles=['PySparkDoc2Vec/Preprocessing.py',
                               'PySparkDoc2Vec/DistDoc2VecFast.py'])

    # sqlContext = SQLContext(sc)
    pos = sc.textFile("hdfs:///movie_review/positive")
    neg = sc.textFile("hdfs:///movie_review/negative")
    both = pos + neg

    dd2v, _ = word2vec(both)
    dd2v, docvecs = doc2vec(dd2v, both)

    dd2v.model.save("PySparkDoc2Vec/Models/review")

    npos = pos.count()
    reg_data = docvecs.zipWithIndex().map(lambda (v, i): LabeledPoint(1.0 if i < npos else 0.0, v))
    regression(reg_data)
